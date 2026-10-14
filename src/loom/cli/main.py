"""`loom`: the user CLI. Talks exclusively to the authenticated Catalog API;
needs no database or IdP administration credentials (see `loom.cli.admin`
for those)."""

import sys

from loom import __version__, default_config_path
from loom.cli.auth import add_auth_parsers
from loom.cli.catalog import add_catalog_parsers
from loom.cli.common import run_cli
from loom.cli.config_cmds import add_config_parsers
from loom.config import UserConfig


def _populate(subparsers) -> None:
    add_config_parsers(subparsers)
    add_auth_parsers(subparsers)
    add_catalog_parsers(subparsers)


def run() -> int:
    return run_cli(
        f'Loom {__version__}',
        default_config_path(),
        UserConfig,
        _populate,
        'LOOM_CONFIG_PATH',
    )


if __name__ == '__main__':
    sys.exit(run())
