import os

from argparse import Namespace
from dataclasses import dataclass, field


@dataclass
class ProjectOptions:
    args: Namespace = field(repr=False, default_factory=Namespace)

    # required arguments
    input_path: str = field(init=False, default_factory=str)

    # build arguments
    no_anonymize: bool = field(init=False, default_factory=bool)
    no_bsarch: bool = field(init=False, default_factory=bool)
    no_incremental_build: bool = field(init=False, default_factory=bool)
    no_parallel: bool = field(init=False, default_factory=bool)

    # game arguments
    game_type: str = field(init=False, default_factory=str)
    game_path: str = field(init=False, default_factory=str)
    registry_path: str = field(init=False, default_factory=str)

    # compiler arguments
    compiler_path: str = field(init=False, default_factory=str)
    flags_path: str = field(init=False, default_factory=str)
    output_path: str = field(init=False, default_factory=str)

    # bsarch arguments
    bsarch_path: str = field(init=False, default_factory=str)
    archive_path: str = field(init=False, default_factory=str)
    temp_path: str = field(init=False, default_factory=str)

    # program arguments
    log_path: str = field(init=False, default_factory=str)

    def __post_init__(self) -> None:
        for key in self.__dict__:
            if key == 'args':
                continue
            try:
                user_value = getattr(self.args, key)
                if user_value and user_value != getattr(self, key):
                    setattr(self, key, user_value)
            except AttributeError:
                continue

    def __setattr__(self, key: str, value: str) -> None:
        # sanitize paths
        if key.endswith('path'):
            value = os.path.normpath(value)
            # normpath converts empty paths to os.curdir which we don't want
            if value == '.':
                value = ''

        if key == 'game_type':
            value = value.casefold()

        super(ProjectOptions, self).__setattr__(key, value)
