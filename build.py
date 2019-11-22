import argparse
import glob
import logging
import os
import shutil
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from glob import glob
from os import makedirs, remove
from os.path import dirname, exists, isfile, join, normpath, relpath
from shutil import copy2, rmtree
from subprocess import check_call, CalledProcessError
from zipfile import ZIP_DEFLATED, ZipFile


class Application:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname).4s] %(message)s')
    log: logging.Logger = logging.getLogger()

    def __init__(self, args: argparse.Namespace) -> None:
        self.root_path: str = os.path.dirname(__file__)

        self.package_name: str = 'pyro'
        self.no_zip: bool = args.no_zip
        self.vcvars64_path: str = args.vcvars64_path

        self.dist_path: str = os.path.join(self.root_path, '%s.dist' % self.package_name)
        self.root_tools_path: str = os.path.join(self.root_path, 'tools')
        self.dist_tools_path: str = os.path.join(self.dist_path, 'tools')

    def __setattr__(self, key: str, value: object) -> None:
        # sanitize paths
        if isinstance(value, str) and key.endswith('path'):
            value = os.path.normpath(value)
            # normpath converts empty paths to os.curdir which we don't want
            if value == '.':
                value = ''
        super(Application, self).__setattr__(key, value)

    def _clean_dist_folder(self) -> None:
        if not os.path.exists(self.dist_path):
            return

        files_to_keep: tuple = (
            '%s.exe' % self.package_name,
            'python37.dll',
            '_multiprocessing.pyd',
            '_queue.pyd',
            '_socket.pyd',
            'select.pyd',
            '_elementpath.pyd',
            'etree.pyd'
        )

        files: list = [f for f in glob(os.path.join(self.dist_path, '**\*'), recursive=True)
                       if os.path.isfile(f) and not f.endswith(files_to_keep)]

        for f in files:
            Application.log.warning('Deleting: "%s"' % f)
            os.remove(f)

        site_dir: str = os.path.join(self.dist_path, 'site')
        if os.path.exists(site_dir):
            shutil.rmtree(site_dir, ignore_errors=True)

    def _build_zip_archive(self) -> str:
        zip_path: str = os.path.join(self.root_path, 'bin', '%s.zip' % self.package_name)
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)

        files: list = [f for f in glob.glob(os.path.join(self.dist_path, '**\*'), recursive=True) if os.path.isfile(f)]

        zip_path: str = join(self.root_path, 'bin', '%s.zip' % self.package_name)
        makedirs(dirname(zip_path), exist_ok=True)

        files = [f for f in glob(join(self.dist_path, '**\*'), recursive=True) if isfile(f)]

        with ZipFile(zip_path, 'w', compression=ZIP_DEFLATED, compresslevel=9) as z:
            for f in files:
                z.write(f, join(self.package_name, relpath(f, self.dist_path)), compress_type=ZIP_DEFLATED)
                print('Added file to archive: %s' % f)

        return zip_path

    def run(self) -> int:
        if not sys.platform == 'win32':
            Application.log.error('Cannot build Pyro with Nuitka on a non-Windows platform')
            sys.exit(1)

        if self.vcvars64_path:
            if not os.path.exists(self.vcvars64_path) or not self.vcvars64_path.endswith('.bat'):
                Application.log.error('Cannot build Pyro with MSVC compiler because VsDevCmd path is invalid')
                sys.exit(1)

        Application.log.info('Using project path: "%s"' % self.root_path)

        Application.log.warning('Cleaning: "%s"' % self.dist_path)
        shutil.rmtree(self.dist_path, ignore_errors=True)

        fail_state: int = 0

        env_log_path: str = ''
        environ: dict = os.environ.copy()

        if self.vcvars64_path:
            try:
                process = subprocess.Popen(f'%comspec% /C "{self.vcvars64_path}"',
                                           stdout=subprocess.PIPE, stderr=None, shell=True, universal_newlines=True)
            except FileNotFoundError:
                fail_state = 1

            # noinspection PyUnboundLocalVariable
            while process.poll() is None:
                line: str = process.stdout.readline().strip()
                Application.log.info(line)

                if 'post-execution' in line:
                    _, env_log_path = line.split(' to ')
                    process.terminate()

            Application.log.info('Loading environment: "%s"' % env_log_path)

            with open(env_log_path, mode='r', encoding='utf-8') as f:
                lines: list = f.read().splitlines()

                for line in lines:
                    key, value = line.split('=', maxsplit=1)
                    environ[key] = value

        args: list = [
            'pipenv',
            'run',
            'nuitka',
            '--standalone', 'pyro',
            '--include-package=pyro',
            '--experimental=use_pefile',
            '--python-flag=nosite',
            '--python-for-scons=%s' % sys.executable,
            '--assume-yes-for-downloads',
            '--plugin-enable=multiprocessing',
            '--show-progress',
            '--file-reference-choice=runtime'
        ]

        command: str = ' '.join(args)

        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False, universal_newlines=True, env=environ)
        except FileNotFoundError:
            fail_state = 1

        if fail_state == 0:
            # noinspection PyUnboundLocalVariable
            while process.poll() is None:
                line = process.stdout.readline().strip()

                if line.startswith(('Courtesy Notice', 'Executing', 'Nuitka:INFO:Optimizing', 'Nuitka:INFO:Doing', 'Nuitka:INFO:Demoting')):
                    continue

                if line.startswith('Error, cannot locate suitable C compiler'):
                    Application.log.error('Cannot locate suitable C compiler.')
                    fail_state = 1
                    break

                if line.startswith('Error, mismatch between') and 'arches' in line:
                    _, message = line.split('between')
                    Application.log.error('Cannot proceed with mismatching architectures: %s' % message.replace(' arches', ''))
                    fail_state = 1
                    break

                if line.startswith('Error'):
                    Application.log.error(line)
                    fail_state = 1
                    break

                if line.startswith('Nuitka:INFO'):
                    line = line.replace('Nuitka:INFO:', '')

                if line.startswith('PASS 2'):
                    line = 'PASS 2:'

                Application.log.info(line)

        if not os.path.exists(self.dist_path) or '%s.exe' % self.package_name not in os.listdir(self.dist_path):
            fail_state = 1

        if fail_state == 0:
            Application.log.info('Removing unnecessary files...')
            self._clean_dist_folder()

            Application.log.info('Copying tools...')
            os.makedirs(self.dist_tools_path, exist_ok=True)

            for tool_file_name in ['bsarch.exe', 'bsarch.license.txt']:
                shutil.copy2(os.path.join(self.root_tools_path, tool_file_name),
                             os.path.join(self.dist_tools_path, tool_file_name))

            if not self.no_zip:
                Application.log.info('Building archive...')
                zip_created: str = self._build_zip_archive()

                Application.log.info('Wrote archive: %s' % zip_created)

            Application.log.info('Build complete.')

            return fail_state

        Application.log.error('Failed to execute command: %s' % command)

        Application.log.warning('Resetting: %s' % self.dist_path)
        shutil.rmtree(self.dist_path, ignore_errors=True)

        return fail_state


if __name__ == '__main__':
    _parser = argparse.ArgumentParser(description='Pyro Build Script')

    _parser.add_argument('--no-zip',
                         action='store_true', default=False,
                         help='do not create zip archive')

    _parser.add_argument('--vcvars64-path',
                         action='store', default='',
                         help='path to visual studio developer command prompt')

    Application(_parser.parse_args()).run()
