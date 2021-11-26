from pyro.Constant import Constant


class FlagsName(Constant):
    FO4: str = 'Institute_Papyrus_Flags.flg'
    SSE: str = 'TESV_Papyrus_Flags.flg'
    TES5: str = 'TESV_Papyrus_Flags.flg'


class GameName(Constant):
    FO4: str = 'Fallout 4'
    SSE: str = 'Skyrim Special Edition'
    TES5: str = 'Skyrim'


class GameType(Constant):
    FO4: str = 'fo4'
    SSE: str = 'sse'
    TES5: str = 'tes5'


class XmlAttributeName(Constant):
    ANONYMIZE: str = 'Anonymize'
    COMPRESSION: str = 'Compression'
    DESCRIPTION: str = 'Description'
    EXCLUDE: str = 'Exclude'
    FINAL: str = 'Final'
    FLAGS: str = 'Flags'
    GAME: str = 'Game'
    IN: str = 'In'
    NAME: str = 'Name'
    NO_RECURSE: str = 'NoRecurse'
    OPTIMIZE: str = 'Optimize'
    OUTPUT: str = 'Output'
    PACKAGE: str = 'Package'
    PATH: str = 'Path'
    RELEASE: str = 'Release'
    REWRITE_TO_PATH: str = 'RewriteToPath'
    ROOT_DIR: str = 'RootDir'
    USE_IN_BUILD: str = 'UseInBuild'
    VALUE: str = 'Value'
    ZIP: str = 'Zip'


class XmlTagName(Constant):
    FOLDER: str = 'Folder'
    FOLDERS: str = 'Folders'
    IMPORT: str = 'Import'
    IMPORTS: str = 'Imports'
    INCLUDE: str = 'Include'
    MATCH: str = 'Match'
    PACKAGE: str = 'Package'
    PACKAGES: str = 'Packages'
    PAPYRUS_PROJECT: str = 'PapyrusProject'
    POST_BUILD_EVENT: str = 'PostBuildEvent'
    POST_IMPORT_EVENT: str = 'PostImportEvent'
    PRE_BUILD_EVENT: str = 'PreBuildEvent'
    PRE_IMPORT_EVENT: str = 'PreImportEvent'
    SCRIPTS: str = 'Scripts'
    VARIABLES: str = 'Variables'
    ZIP_FILE: str = 'ZipFile'
    ZIP_FILES: str = 'ZipFiles'


__all__ = ['FlagsName', 'GameName', 'GameType', 'XmlAttributeName', 'XmlTagName']
