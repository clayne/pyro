import hashlib
import io
import os
import sys
import typing
import zipfile
from copy import deepcopy
from urllib.parse import urlparse

from lxml import etree

from pyro.CommandArguments import CommandArguments
from pyro.PathHelper import PathHelper
from pyro.PexReader import PexReader
from pyro.ProjectBase import ProjectBase
from pyro.ProjectOptions import ProjectOptions
from pyro.Remotes import GenericRemote, RemoteBase
from pyro.XmlHelper import XmlHelper
from pyro.XmlRoot import XmlRoot


class PapyrusProject(ProjectBase):
    ppj_root: XmlRoot = None
    folders_node: etree.ElementBase = None
    imports_node: etree.ElementBase = None
    packages_node: etree.ElementBase = None
    scripts_node: etree.ElementBase = None
    zip_file_node: etree.ElementBase = None

    has_folders_node: bool = False
    has_imports_node: bool = False
    has_packages_node: bool = False
    has_scripts_node: bool = False
    has_zip_file_node: bool = False

    remote: RemoteBase = None
    remote_schemas: tuple = ('http:', 'https:')

    namespace: str = ''
    zip_file_name: str = ''
    zip_root_path: str = ''

    missing_scripts: list = []
    pex_paths: list = []
    psc_paths: list = []

    def __init__(self, options: ProjectOptions) -> None:
        super(PapyrusProject, self).__init__(options)

        xml_parser: etree.XMLParser = etree.XMLParser(remove_blank_text=True, remove_comments=True)

        # strip comments from raw text because lxml.etree.XMLParser does not remove XML-unsupported comments
        # e.g., '<PapyrusProject <!-- xmlns="PapyrusProject.xsd" -->>'
        xml_document: io.StringIO = XmlHelper.strip_xml_comments(self.options.input_path)

        project_xml: etree.ElementTree = etree.parse(xml_document, xml_parser)

        self.ppj_root = XmlRoot(project_xml)

        schema: etree.XMLSchema = XmlHelper.validate_schema(self.ppj_root.ns, self.program_path)

        if schema:
            try:
                schema.assertValid(project_xml)
            except etree.DocumentInvalid as e:
                PapyrusProject.log.error(f'Failed to validate XML Schema.{os.linesep}\t{e}')
                sys.exit(1)
            else:
                PapyrusProject.log.info('Successfully validated XML Schema.')

        # variables need to be parsed before nodes are updated
        variables_node = self.ppj_root.find('Variables')
        if variables_node is not None:
            self._parse_variables(variables_node)

        # we need to parse all attributes after validating and before we do anything else
        # options can be overridden by arguments when the BuildFacade is initialized
        self._update_attributes(self.ppj_root.node)

        if self.options.resolve_ppj:
            xml_output = etree.tostring(self.ppj_root.node, encoding='utf-8', xml_declaration=True, pretty_print=True)
            PapyrusProject.log.debug(f'Resolved PPJ. Text output:{os.linesep * 2}{xml_output.decode()}')
            sys.exit(1)

        self.options.flags_path = self.ppj_root.get('Flags')
        self.options.output_path = self.ppj_root.get('Output')

        self.optimize = self.ppj_root.get('Optimize') == 'True'
        self.release = self.ppj_root.get('Release') == 'True'
        self.final = self.ppj_root.get('Final') == 'True'

        self.options.anonymize = self.ppj_root.get('Anonymize') == 'True'
        self.options.package = self.ppj_root.get('Package') == 'True'
        self.options.zip = self.ppj_root.get('Zip') == 'True'

        self.imports_node = self.ppj_root.find('Imports')
        self.has_imports_node = self.imports_node is not None

        self.scripts_node = self.ppj_root.find('Scripts')
        self.has_scripts_node = self.scripts_node is not None

        self.folders_node = self.ppj_root.find('Folders')
        self.has_folders_node = self.folders_node is not None

        self.packages_node = self.ppj_root.find('Packages')
        self.has_packages_node = self.packages_node is not None

        self.zip_file_node = self.ppj_root.find('ZipFile')
        self.has_zip_file_node = self.zip_file_node is not None

        if self.options.package and self.has_packages_node:
            self.options.package_path = self.packages_node.get('Output')

        if self.options.zip and self.has_zip_file_node:
            self.zip_file_name = self.zip_file_node.get('Name')
            self.zip_root_path = self.zip_file_node.get('RootDir')
            self.options.zip_output_path = self.zip_file_node.get('Output')
            self.options.zip_compression = self.zip_file_node.get('Compression').casefold()
            self._setup_zipfile_options()

        # initialize remote if needed
        if self.remote_paths:
            if not self.options.remote_temp_path:
                self.options.remote_temp_path = self.get_remote_temp_path()

            if self.options.access_token:
                self.remote = GenericRemote(self.options.access_token)
            else:
                PapyrusProject.log.error('Cannot proceed without personal access token')
                sys.exit(1)

            # validate remote paths
            for path in self.remote_paths:
                if not self.remote.validate_url(path):
                    PapyrusProject.log.error(f'Cannot proceed while node contains invalid URL: "{path}"')
                    sys.exit(1)

        # we need to populate the list of import paths before we try to determine the game type
        # because the game type can be determined from import paths
        self.import_paths = self._get_import_paths()
        if not self.import_paths:
            PapyrusProject.log.error('Failed to build list of import paths')
            sys.exit(1)

        # ensure that folder paths are implicitly imported
        implicit_folder_paths: list = self._get_implicit_folder_imports()
        PathHelper.merge_implicit_import_paths(implicit_folder_paths, self.import_paths)

        # we need to populate psc paths after explicit and implicit import paths are populated
        # this also needs to be set before we populate implicit import paths from psc paths
        # not sure if this must run again after populating implicit import paths from psc paths
        self.psc_paths = self._get_psc_paths()
        if not self.psc_paths:
            PapyrusProject.log.error('Failed to build list of script paths')
            sys.exit(1)

        # this adds implicit imports from script paths
        implicit_script_paths: list = self._get_implicit_script_imports()
        PathHelper.merge_implicit_import_paths(implicit_script_paths, self.import_paths)

        for path in (p for p in implicit_folder_paths + implicit_script_paths if p in self.import_paths):
            PapyrusProject.log.warning(f'Using import path implicitly: "{path}"')

        # we need to set the game type after imports are populated but before pex paths are populated
        # allow xml to set game type but defer to passed argument
        if not self.options.game_type:
            game_type: str = self.ppj_root.get('Game', default='').casefold()

            if game_type and game_type in self.game_types:
                valid_game_type: str = self.game_types[game_type]
                PapyrusProject.log.warning(f'Using game type: {valid_game_type} (determined from Papyrus Project)')
                self.options.game_type = game_type

        if not self.options.game_type:
            self.options.game_type = self.get_game_type()

        if not self.options.game_type:
            PapyrusProject.log.error('Cannot determine game type from arguments or Papyrus Project')
            sys.exit(1)

        # get expected pex paths - these paths may not exist and that is okay!
        self.pex_paths = self._get_pex_paths()

        # these are relative paths to psc scripts whose pex counterparts are missing
        self.missing_scripts = self._find_missing_script_paths()

        # game type must be set before we call this
        if not self.options.game_path:
            self.options.game_path = self.get_game_path()

    @property
    def remote_paths(self) -> list:
        """
        Collects list of remote paths from Import and Folder nodes
        """
        results = []

        if self.has_imports_node:
            for node in self.imports_node:
                if not node.tag.endswith('Import'):
                    continue

                if node.text.casefold().startswith(self.remote_schemas):
                    results.append(node.text)

        if self.has_folders_node:
            for node in self.folders_node:
                if not node.tag.endswith('Folder'):
                    continue

                if node.text.casefold().startswith(self.remote_schemas):
                    results.append(node.text)

        return results

    def _parse_variables(self, variables_node: etree.ElementBase) -> None:
        reserved_characters: tuple = ('!', '#', '$', '%', '^', '&', '*')

        for variable_node in variables_node:
            if not variable_node.tag.endswith('Variable'):
                continue

            var_key = variable_node.get('Name', default='')
            var_value = variable_node.get('Value', default='')

            if any([not var_key, not var_value]):
                continue

            if not var_key.isalnum():
                PapyrusProject.log.error(f'The name of the variable "{var_key}" must be an alphanumeric string.')
                sys.exit(1)

            if any(c in reserved_characters for c in var_value):
                PapyrusProject.log.error(f'The value of the variable "{var_key}" contains a reserved character.')
                sys.exit(1)

            self.variables.update({var_key: var_value})

        # allow variables to reference other variables
        for var_key, var_value in self.variables.items():
            self.variables.update({var_key: self.parse(var_value)})

        # complete round trip so that order does not matter
        for var_key in reversed([var_key for var_key in self.variables]):
            var_value = self.variables[var_key]
            self.variables.update({var_key: self.parse(var_value)})

    def _update_attributes(self, parent_node: etree.ElementBase) -> None:
        """Updates attributes of element tree with missing attributes and default values"""
        bool_keys = ['Optimize', 'Release', 'Final', 'Anonymize', 'Package', 'Zip']

        for node in parent_node.getiterator():
            if node.text:
                node.text = self.parse(node.text.strip())

            if not node.attrib:
                continue

            tag = node.tag.replace('{%s}' % self.namespace, '')

            if tag == 'PapyrusProject':
                if 'Game' not in node.attrib:
                    node.set('Game', '')
                if 'Flags' not in node.attrib:
                    node.set('Flags', self.options.flags_path)
                if 'Output' not in node.attrib:
                    node.set('Output', self.options.output_path)
                for key in bool_keys:
                    if key not in node.attrib:
                        node.set(key, 'False')

            elif tag == 'Packages':
                if 'Output' not in node.attrib:
                    node.set('Output', self.options.package_path)

            elif tag == 'Package':
                if 'Name' not in node.attrib:
                    node.set('Name', self.project_name)
                if 'RootDir' not in node.attrib:
                    node.set('RootDir', self.project_path)

            elif tag in ('Folder', 'Include'):
                if 'NoRecurse' not in node.attrib:
                    node.set('NoRecurse', 'False')

            elif tag == 'ZipFile':
                if 'Name' not in node.attrib:
                    node.set('Name', self.project_name)
                if 'RootDir' not in node.attrib:
                    node.set('RootDir', self.project_path)
                if 'Output' not in node.attrib:
                    node.set('Output', self.options.zip_output_path)
                if 'Compression' not in node.attrib:
                    node.set('Compression', 'deflate')

            # parse values
            for key, value in node.attrib.items():
                value = value.casefold() in ('true', '1') if key in bool_keys + ['NoRecurse'] else self.parse(value)
                node.set(key, str(value))

    def _setup_zipfile_options(self) -> None:
        # zip - required attribute
        if not os.path.isabs(self.zip_root_path):
            test_path: str = os.path.normpath(os.path.join(self.project_path, self.zip_root_path))

            if os.path.isdir(test_path):
                self.zip_root_path = test_path
            else:
                PapyrusProject.log.error(f'Cannot resolve RootDir path to existing folder: "{self.zip_root_path}"')
                sys.exit(1)

        # zip - optional attributes
        if not self.zip_file_name.casefold().endswith('.zip'):
            self.zip_file_name = f'{self.zip_file_name}.zip'

        if self.options.zip_compression not in ('store', 'deflate'):
            self.options.zip_compression = 'deflate'

        use_store = self.options.zip_compression == 'store'
        self.compress_type: int = zipfile.ZIP_STORED if use_store else zipfile.ZIP_DEFLATED

    def _calculate_object_name(self, psc_path: str) -> str:
        if self.options.game_type == 'fo4':
            return PathHelper.calculate_relative_object_name(psc_path, self.import_paths)
        return os.path.basename(psc_path)

    @staticmethod
    def _can_remove_folder(import_path: str, object_name: str, script_path: str) -> bool:
        import_path = import_path.casefold()
        object_name = object_name.casefold()
        script_path = script_path.casefold()
        return script_path.startswith(import_path) and os.path.join(import_path, object_name) != script_path

    def _find_missing_script_paths(self) -> list:
        """Returns list of script paths for compiled scripts that do not exist"""
        results: list = []

        for psc_path in self.psc_paths:
            object_name = self._calculate_object_name(psc_path)

            pex_path: str = os.path.join(self.options.output_path, object_name.replace('.psc', '.pex'))
            if os.path.isfile(pex_path):
                continue

            if psc_path not in results:
                results.append(psc_path)

        return results

    def _get_import_paths(self) -> list:
        """Returns absolute import paths from Papyrus Project"""
        results: list = []

        if not self.has_imports_node:
            return []

        for import_node in self.imports_node:
            if not import_node.tag.endswith('Import'):
                continue

            if not import_node.text:
                continue

            if import_node.text.casefold().startswith(self.remote_schemas):
                local_path = self._get_remote_path(import_node)
                PapyrusProject.log.info(f'Adding import path from remote: "{local_path}"...')
                results.append(local_path)
                continue

            import_path = os.path.normpath(import_node.text)

            if import_path == os.pardir:
                self.log.warning(f'Import paths cannot be equal to "{os.pardir}"')
                continue

            if import_path == os.curdir:
                import_path = self.project_path
            elif not os.path.isabs(import_path):
                # relative import paths should be relative to the project
                import_path = os.path.normpath(os.path.join(self.project_path, import_path))

            if os.path.isdir(import_path):
                results.append(import_path)

        return PathHelper.uniqify(results)

    def _get_implicit_folder_imports(self) -> list:
        """Returns absolute implicit import paths from Folder node paths"""
        implicit_paths: list = []

        if not self.has_folders_node:
            return []

        for folder_node in self.folders_node:
            if not folder_node.tag.endswith('Folder'):
                continue

            if not folder_node.text:
                continue

            folder_path: str = os.path.normpath(folder_node.text)

            if os.path.isabs(folder_path):
                if os.path.isdir(folder_path):
                    implicit_paths.append(folder_path)
            else:
                test_path = os.path.join(self.project_path, folder_path)
                if os.path.isdir(test_path):
                    implicit_paths.append(test_path)

        return PathHelper.uniqify(implicit_paths)

    def _get_implicit_script_imports(self) -> list:
        """Returns absolute implicit import paths from Script node paths"""
        implicit_paths: list = []

        for psc_path in self.psc_paths:
            script_folder_path = os.path.dirname(psc_path)

            for import_path in self.import_paths:
                relpath = os.path.relpath(script_folder_path, import_path)

                test_path = os.path.normpath(os.path.join(import_path, relpath))
                if os.path.isdir(test_path):
                    implicit_paths.append(test_path)

        return PathHelper.uniqify(implicit_paths)

    def _get_pex_paths(self) -> list:
        """
        Returns absolute paths to compiled scripts that may not exist yet in output folder
        """
        pex_paths: list = []

        for psc_path in self.psc_paths:
            object_name = self._calculate_object_name(psc_path)

            pex_path = os.path.join(self.options.output_path, object_name.replace('.psc', '.pex'))

            if pex_path not in pex_paths:
                pex_paths.append(pex_path)

        return pex_paths

    def _get_psc_paths(self) -> list:
        """Returns script paths from Folders and Scripts nodes"""
        paths: set = set()

        # try to populate paths with scripts from Folders and Scripts nodes
        if self.has_folders_node:
            for script_path in self._get_script_paths_from_folders_node():
                paths.add(script_path)

        if self.has_scripts_node:
            for script_path in self._get_script_paths_from_scripts_node():
                paths.add(script_path)

        results: list = []

        # convert user paths to absolute paths
        for path in paths:
            # try to add existing absolute paths
            if os.path.isabs(path) and os.path.isfile(path):
                results.append(path)
                continue

            # try to add existing project-relative paths
            test_path = os.path.join(self.project_path, path)
            if os.path.isfile(test_path):
                results.append(test_path)
                continue

            # try to add existing import-relative paths
            for import_path in self.import_paths:
                if not os.path.isabs(import_path):
                    import_path = os.path.join(self.project_path, import_path)

                test_path = os.path.join(import_path, path)
                if os.path.isfile(test_path):
                    results.append(test_path)
                    break

        results = PathHelper.uniqify(results)

        PapyrusProject.log.info(f'{len(results)} unique script paths resolved to absolute paths.')

        return results

    def _get_remote_path(self, node: etree.ElementBase) -> str:
        url_hash = hashlib.sha1(node.text.encode()).hexdigest()[:8]
        temp_path = os.path.join(self.options.remote_temp_path, url_hash)

        if self.options.force_overwrite or not os.path.exists(temp_path):
            for message in self.remote.fetch_contents(node.text, temp_path):
                if not message.startswith('Failed to load'):
                    PapyrusProject.log.info(message)
                else:
                    PapyrusProject.log.error(message)
                    sys.exit(1)

        parsed_url = urlparse(node.text)

        if parsed_url.netloc == 'api.github.com':
            url_path_parts = parsed_url.path.split('/')[2:]
            url_path_parts.pop(2)  # pop 'contents'
            url_path = os.sep.join(url_path_parts)
        elif parsed_url.netloc == 'github.com':
            url_path_parts = parsed_url.path.split('/')[1:]
            url_path_parts.pop(3)  # pop 'master' (or any other branch)
            url_path_parts.pop(2)  # pop 'tree'
            url_path = os.sep.join(url_path_parts)
        else:
            raise NotImplementedError

        local_path = os.path.join(temp_path, url_path)

        return local_path

    def _get_script_paths_from_folders_node(self) -> typing.Generator:
        """Returns script paths from the Folders element array"""
        for folder_node in self.folders_node:
            if not folder_node.tag.endswith('Folder'):
                continue

            if folder_node.text == os.pardir:
                self.log.warning(f'Folder paths cannot be equal to "{os.pardir}"')
                continue

            no_recurse: bool = folder_node.get('NoRecurse') == 'True'

            # try to add project path
            if folder_node.text == os.curdir:
                yield from PathHelper.find_script_paths_from_folder(self.project_path, no_recurse)
                continue

            if folder_node.text.casefold().startswith(self.remote_schemas):
                local_path = self._get_remote_path(folder_node)
                PapyrusProject.log.info(f'Adding import path from remote: "{local_path}"...')
                self.import_paths.insert(0, local_path)
                PapyrusProject.log.info(f'Adding folder path from remote: "{local_path}"...')
                yield from PathHelper.find_script_paths_from_folder(local_path, no_recurse)
                continue

            folder_path: str = os.path.normpath(folder_node.text)

            # try to add absolute path
            if os.path.isabs(folder_path) and os.path.isdir(folder_path):
                yield from PathHelper.find_script_paths_from_folder(folder_path, no_recurse)
                continue

            # try to add project-relative folder path
            test_path = os.path.join(self.project_path, folder_path)
            if os.path.isdir(test_path):
                yield from PathHelper.find_script_paths_from_folder(test_path, no_recurse)
                continue

            # try to add import-relative folder path
            for import_path in self.import_paths:
                test_path = os.path.join(import_path, folder_path)
                if os.path.isdir(test_path):
                    yield from PathHelper.find_script_paths_from_folder(test_path, no_recurse)

    def _get_script_paths_from_scripts_node(self) -> typing.Generator:
        """Returns script paths from the Scripts node"""
        for script_node in self.scripts_node:
            if not script_node.tag.endswith('Script'):
                continue

            if not script_node.text:
                continue

            if ':' in script_node.text:
                script_node.text = script_node.text.replace(':', os.sep)

            yield os.path.normpath(script_node.text)

    def _try_exclude_unmodified_scripts(self) -> set:
        psc_paths: set = set()

        for psc_path in self.psc_paths:
            script_name, _ = os.path.splitext(os.path.basename(psc_path))

            # if pex exists, compare time_t in pex header with psc's last modified timestamp
            matching_path: str = ''
            for pex_path in self.pex_paths:
                if pex_path.endswith(f'{script_name}.pex'):
                    matching_path = pex_path
                    break

            if not os.path.isfile(matching_path):
                continue

            try:
                header = PexReader.get_header(matching_path)
            except ValueError:
                PapyrusProject.log.warning(f'Cannot determine compilation time due to unknown magic: "{matching_path}"')
                continue

            compiled_time: int = header.compilation_time.value
            if os.path.getmtime(psc_path) < compiled_time:
                continue

            if psc_path not in psc_paths:
                psc_paths.add(psc_path)

        return psc_paths

    def build_commands(self) -> list:
        """
        Builds list of commands for compiling scripts
        """
        commands: list = []

        arguments = CommandArguments()

        compiler_path: str = self.options.compiler_path
        flags_path: str = self.options.flags_path
        output_path: str = self.options.output_path

        if self.options.no_incremental_build:
            psc_paths = PathHelper.uniqify(self.psc_paths)
        else:
            psc_paths = list(self._try_exclude_unmodified_scripts())

        # add .psc scripts whose .pex counterparts do not exist
        for missing_psc_path in [p for p in self.missing_scripts if p not in psc_paths]:
            psc_paths.append(missing_psc_path)

        source_import_paths = deepcopy(self.import_paths)

        # TODO: depth sorting solution is not foolproof! parse psc files for imports to determine command order
        for script_path in sorted(psc_paths, key=lambda p: (p.count(os.sep), len(p)), reverse=True):
            import_paths: list = self.import_paths

            object_name: str = script_path

            if self.options.game_type == 'fo4':
                object_name = PathHelper.calculate_relative_object_name(script_path, self.import_paths)

                # remove unnecessary import paths for script
                for import_path in reversed(self.import_paths):
                    if self._can_remove_folder(import_path, object_name, script_path):
                        import_paths.remove(import_path)

            arguments.clear()
            arguments.append(compiler_path, enquote_value=True)
            arguments.append(object_name, enquote_value=True)
            arguments.append(output_path, key='o', enquote_value=True)
            arguments.append(';'.join(import_paths), key='i', enquote_value=True)
            arguments.append(flags_path, key='f', enquote_value=True)

            if self.options.game_type == 'fo4':
                # noinspection PyUnboundLocalVariable
                if self.release:
                    arguments.append('-release')

                # noinspection PyUnboundLocalVariable
                if self.final:
                    arguments.append('-final')

            if self.optimize:
                arguments.append('-op')

            commands.append(arguments.join())

        self.import_paths = source_import_paths

        return commands
