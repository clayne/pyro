import logging
import os
import shutil
import sys
import typing
import zipfile

from lxml import etree
from wcmatch import (glob,
                     wcmatch)

from pyro.CommandArguments import CommandArguments
from pyro.Comparators import (endswith,
                              is_include_node,
                              is_match_node,
                              is_package_node,
                              is_zipfile_node,
                              startswith)
from pyro.CaseInsensitiveList import CaseInsensitiveList
from pyro.Constants import (GameType,
                            XmlAttributeName,
                            XmlTagName)
from pyro.Enums.ZipCompression import ZipCompression
from pyro.PapyrusProject import PapyrusProject
from pyro.ProcessManager import ProcessManager
from pyro.ProjectOptions import ProjectOptions


class PackageManager:
    log: logging.Logger = logging.getLogger('pyro')

    ppj: PapyrusProject = None
    options: ProjectOptions = None
    pak_extension: str = ''
    zip_extension: str = ''

    DEFAULT_GLFLAGS = glob.NODIR | glob.MATCHBASE | glob.SPLIT | glob.REALPATH | glob.FOLLOW | glob.IGNORECASE | glob.MINUSNEGATE
    DEFAULT_WCFLAGS = wcmatch.SYMLINKS | wcmatch.IGNORECASE | wcmatch.MINUSNEGATE

    includes: int = 0

    def __init__(self, ppj: PapyrusProject) -> None:
        self.ppj = ppj
        self.options = ppj.options

        self.pak_extension = '.ba2' if self.options.game_type == GameType.FO4 else '.bsa'
        self.zip_extension = '.zip'

    @staticmethod
    def _can_compress_package(containing_folder: str):
        flags = wcmatch.RECURSIVE | wcmatch.IGNORECASE

        # voices bad because bethesda no likey
        for _ in wcmatch.WcMatch(containing_folder, '*.fuz', flags=flags).imatch():
            return False

        # sounds bad because bethesda no likey
        for _ in wcmatch.WcMatch(containing_folder, '*.wav|*.xwm', flags=flags).imatch():
            return False

        # strings bad because wrye bash no likey
        for _ in wcmatch.WcMatch(containing_folder, '*.*strings', flags=flags).imatch():
            return False

        return True

    @staticmethod
    def _check_write_permission(file_path: str) -> None:
        if os.path.isfile(file_path):
            try:
                open(file_path, 'a').close()
            except PermissionError:
                PackageManager.log.error(f'Cannot create file without write permission to: "{file_path}"')
                sys.exit(1)

    @staticmethod
    def _match(root_dir: str, file_pattern: str, *, exclude_pattern: str = '', user_path: str = '', no_recurse: bool = False, rewrite_to_path: bool = False) -> typing.Generator:
        user_flags = wcmatch.RECURSIVE if not no_recurse else 0x0
        matcher = wcmatch.WcMatch(root_dir, file_pattern,
                                  exclude_pattern=exclude_pattern,
                                  flags=PackageManager.DEFAULT_WCFLAGS | user_flags)

        matcher.on_reset()
        matcher._skipped = 0
        for file_path in matcher._walk():
            yield file_path, user_path, rewrite_to_path

    @staticmethod
    def _generate_include_paths(includes_node: etree.ElementBase, root_path: str, zip_mode: bool = False) -> typing.Generator:
        for include_node in filter(is_include_node, includes_node):
            attr_no_recurse: bool = include_node.get(XmlAttributeName.NO_RECURSE) == 'True'
            attr_path: str = include_node.get(XmlAttributeName.PATH).strip()
            attr_rewrite_to_path: bool = include_node.get(XmlAttributeName.REWRITE_TO_PATH) == 'True'
            search_path: str = include_node.text

            if not search_path:
                PackageManager.log.error(f'Include path at line {include_node.sourceline} in project file is empty')
                sys.exit(1)

            if not zip_mode and startswith(search_path, os.pardir):
                PackageManager.log.error(f'Include paths cannot start with "{os.pardir}"')
                sys.exit(1)

            if zip_mode and attr_rewrite_to_path:
                PackageManager.log.error(f'{XmlAttributeName.REWRITE_TO_PATH} attribute is only available on {XmlTagName.PACKAGES}.{XmlTagName.PACKAGE}.{XmlTagName.INCLUDE} elements')
                sys.exit(1)

            if attr_rewrite_to_path and attr_path == '':
                PackageManager.log.error(f'{XmlAttributeName.PATH} attribute must be defined and not be empty when using the {XmlAttributeName.REWRITE_TO_PATH} attribute')
                sys.exit(1)

            if startswith(search_path, os.curdir):
                search_path = search_path.replace(os.curdir, root_path, 1)

            # fix invalid pattern with leading separator
            if not zip_mode and startswith(search_path, (os.path.sep, os.path.altsep)):
                search_path = '**' + search_path

            if '\\' in search_path:
                search_path = search_path.replace('\\', '/')

            # populate files list using glob patterns or relative paths
            if '*' in search_path:
                for include_path in glob.iglob(search_path,
                                               root_dir=root_path,
                                               flags=PackageManager.DEFAULT_GLFLAGS | glob.GLOBSTAR if not attr_no_recurse else 0x0):
                    yield os.path.join(root_path, include_path), attr_path, attr_rewrite_to_path

            elif not os.path.isabs(search_path):
                test_path = os.path.normpath(os.path.join(root_path, search_path))
                if os.path.isfile(test_path):
                    yield test_path, attr_path
                elif os.path.isdir(test_path):
                    yield from PackageManager._match(test_path, '*.*',
                                                     user_path=attr_path,
                                                     no_recurse=attr_no_recurse,
                                                     rewrite_to_path=attr_rewrite_to_path)
                else:
                    for include_path in glob.iglob(search_path,
                                                   root_dir=root_path,
                                                   flags=PackageManager.DEFAULT_GLFLAGS | glob.GLOBSTAR if not attr_no_recurse else 0x0):
                        yield os.path.join(root_path, include_path), attr_path, attr_rewrite_to_path

            # populate files list using absolute paths
            else:
                if not zip_mode and root_path not in search_path:
                    PackageManager.log.error(f'Cannot include path outside RootDir: "{search_path}"')
                    sys.exit(1)

                search_path = os.path.abspath(os.path.normpath(search_path))

                if os.path.isfile(search_path):
                    yield search_path, attr_path, attr_rewrite_to_path
                else:
                    yield from PackageManager._match(search_path, '*.*',
                                                     user_path=attr_path,
                                                     no_recurse=attr_no_recurse)

        for match_node in filter(is_match_node, includes_node):
            attr_in: str = match_node.get(XmlAttributeName.IN).strip()
            attr_no_recurse: bool = match_node.get(XmlAttributeName.NO_RECURSE) == 'True'
            attr_exclude: str = match_node.get(XmlAttributeName.EXCLUDE).strip()
            attr_path: str = match_node.get(XmlAttributeName.PATH).strip()

            in_path: str = os.path.normpath(attr_in)

            if in_path == os.pardir or startswith(in_path, os.pardir):
                in_path = in_path.replace(os.pardir, os.path.normpath(os.path.join(root_path, os.pardir)), 1)
            elif in_path == os.curdir or startswith(in_path, os.curdir):
                in_path = in_path.replace(os.curdir, root_path, 1)

            if not os.path.isabs(in_path):
                in_path = os.path.join(root_path, in_path)
            elif zip_mode and root_path not in in_path:
                PackageManager.log.error(f'Cannot match path outside RootDir: "{in_path}"')
                sys.exit(1)

            if not os.path.isdir(in_path):
                PackageManager.log.error(f'Cannot match path that does not exist or is not a directory: "{in_path}"')
                sys.exit(1)

            match_text: str = match_node.text

            if startswith(match_text, '.'):
                PackageManager.log.error(f'Match pattern at line {match_node.sourceline} in project file is not a valid wildcard pattern')
                sys.exit(1)

            yield from PackageManager._match(in_path, match_text,
                                             exclude_pattern=attr_exclude,
                                             user_path=attr_path,
                                             no_recurse=attr_no_recurse)

    def _fix_package_extension(self, package_name: str) -> str:
        if not endswith(package_name, ('.ba2', '.bsa'), ignorecase=True):
            return f'{package_name}{self.pak_extension}'
        return f'{os.path.splitext(package_name)[0]}{self.pak_extension}'

    def _fix_zip_extension(self, zip_name: str) -> str:
        if not endswith(zip_name, '.zip', ignorecase=True):
            return f'{zip_name}{self.zip_extension}'
        return f'{os.path.splitext(zip_name)[0]}{self.zip_extension}'

    def _try_resolve_project_relative_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path

        test_path: str = os.path.normpath(os.path.join(self.ppj.project_path, path))

        return test_path if os.path.isdir(test_path) else ''

    def build_commands(self, containing_folder: str, output_path: str) -> str:
        """
        Builds command for creating package with BSArch
        """
        arguments = CommandArguments()

        arguments.append(self.options.bsarch_path, enquote_value=True)
        arguments.append('pack')
        arguments.append(containing_folder, enquote_value=True)
        arguments.append(output_path, enquote_value=True)

        compressed_package = PackageManager._can_compress_package(containing_folder)

        flags = wcmatch.RECURSIVE | wcmatch.IGNORECASE

        if self.options.game_type == GameType.FO4:
            for _ in wcmatch.WcMatch(containing_folder, '!*.dds', flags=flags).imatch():
                arguments.append('-fo4')
                break
            else:
                arguments.append('-fo4dds')
        elif self.options.game_type == GameType.SSE:
            arguments.append('-sse')

            if not compressed_package:
                # SSE crashes when uncompressed BSA has Embed Filenames flag and contains textures
                for _ in wcmatch.WcMatch(containing_folder, '*.dds', flags=flags).imatch():
                    arguments.append('-af:0x3')
                    break
        else:
            arguments.append('-tes5')

        # binary identical files share same data to preserve space
        arguments.append('-share')

        if compressed_package:
            arguments.append('-z')

        return arguments.join()

    def create_packages(self) -> None:
        # clear temporary data
        if os.path.isdir(self.options.temp_path):
            shutil.rmtree(self.options.temp_path, ignore_errors=True)

        # ensure package path exists
        if not os.path.isdir(self.options.package_path):
            os.makedirs(self.options.package_path, exist_ok=True)

        file_names = CaseInsensitiveList()

        for i, package_node in enumerate(filter(is_package_node, self.ppj.packages_node)):
            attr_file_name: str = package_node.get(XmlAttributeName.NAME)

            # noinspection PyProtectedMember
            root_dir: str = self.ppj._get_path(package_node.get(XmlAttributeName.ROOT_DIR),
                                               relative_root_path=self.ppj.project_path,
                                               fallback_path=[self.ppj.project_path, os.path.basename(attr_file_name)])

            # prevent clobbering files previously created in this session
            if attr_file_name in file_names:
                attr_file_name = f'{self.ppj.project_name} ({i})'

            if attr_file_name not in file_names:
                file_names.append(attr_file_name)

            attr_file_name = self._fix_package_extension(attr_file_name)

            file_path: str = os.path.join(self.options.package_path, attr_file_name)

            self._check_write_permission(file_path)

            PackageManager.log.info(f'Creating "{attr_file_name}"...')

            for source_path, attr_path, attr_rewrite_to_path in self._generate_include_paths(package_node, root_dir):
                if os.path.isabs(source_path):
                    relpath: str = os.path.relpath(source_path, root_dir)
                else:
                    relpath: str = source_path
                    source_path = os.path.join(self.ppj.project_path, source_path)

                if attr_rewrite_to_path:
                    adj_relpath = os.path.normpath(os.path.join(attr_path, os.path.basename(relpath)))
                    PackageManager.log.info(f'+ "{relpath.casefold()}" -> "{adj_relpath.casefold()}"')
                else:
                    adj_relpath = os.path.normpath(os.path.join(attr_path, relpath))
                    PackageManager.log.info(f'+ "{adj_relpath.casefold()}"')

                target_path: str = os.path.join(self.options.temp_path, adj_relpath)

                # fix target path if user passes a deeper package root (RootDir)
                if endswith(source_path, '.pex', ignorecase=True) and not startswith(relpath, 'scripts', ignorecase=True) and not attr_rewrite_to_path:
                    target_path = os.path.join(self.options.temp_path, 'Scripts', relpath)

                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                shutil.copy2(source_path, target_path)

                self.includes += 1

            # run bsarch
            command: str = self.build_commands(self.options.temp_path, file_path)
            ProcessManager.run_bsarch(command)

            # clear temporary data
            if os.path.isdir(self.options.temp_path):
                shutil.rmtree(self.options.temp_path, ignore_errors=True)

    def create_zip(self) -> None:
        # ensure zip output path exists
        if not os.path.isdir(self.options.zip_output_path):
            os.makedirs(self.options.zip_output_path, exist_ok=True)

        file_names = CaseInsensitiveList()

        for i, zip_node in enumerate(filter(is_zipfile_node, self.ppj.zip_files_node)):
            attr_file_name: str = zip_node.get(XmlAttributeName.NAME)

            # prevent clobbering files previously created in this session
            if attr_file_name in file_names:
                attr_file_name = f'{attr_file_name} ({i})'

            if attr_file_name not in file_names:
                file_names.append(attr_file_name)

            attr_file_name = self._fix_zip_extension(attr_file_name)

            file_path: str = os.path.join(self.options.zip_output_path, attr_file_name)

            self._check_write_permission(file_path)

            if self.options.zip_compression in ('store', 'deflate'):
                compress_type = ZipCompression.get(self.options.zip_compression)
            else:
                compress_str = zip_node.get(XmlAttributeName.COMPRESSION)
                compress_type = ZipCompression.get(compress_str)

            root_dir: str = self.ppj._get_path(zip_node.get(XmlAttributeName.ROOT_DIR),
                                               relative_root_path=self.ppj.project_path,
                                               fallback_path='')

            if root_dir:
                PackageManager.log.info(f'Creating "{attr_file_name}"...')

                try:
                    with zipfile.ZipFile(file_path, mode='w', compression=compress_type) as z:
                        for include_path, attr_path, _ in self._generate_include_paths(zip_node, root_dir, True):
                            if not attr_path:
                                if root_dir in include_path:
                                    arcname = os.path.relpath(include_path, root_dir)
                                else:
                                    # just add file to zip root
                                    arcname = os.path.basename(include_path)
                            else:
                                _, attr_file_name = os.path.split(include_path)
                                arcname = attr_file_name if attr_path == os.curdir else os.path.join(attr_path, attr_file_name)

                            PackageManager.log.info('+ "{}"'.format(arcname))
                            z.write(include_path, arcname, compress_type=compress_type)

                            self.includes += 1

                    PackageManager.log.info(f'Wrote ZIP file: "{file_path}"')
                except PermissionError:
                    PackageManager.log.error(f'Cannot open ZIP file for writing: "{file_path}"')
                    sys.exit(1)
            else:
                PackageManager.log.error(f'Cannot resolve RootDir path to existing folder: "{root_dir}"')
                sys.exit(1)
