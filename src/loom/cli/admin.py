"""`loom-admin`: the Platform Administrator CLI. Holds the infrastructure
credentials (database, IdP admin) that the `loom` user CLI deliberately
doesn't."""

import sys

from loom import __version__, default_admin_config_path
from loom.cli.common import run_cli
from loom.cli.config_cmds import add_config_parsers
from loom.cli.db import add_db_parsers
from loom.cli.idp import add_idp_parsers
from loom.cli.operator import add_operator_parsers
from loom.cli.server_cmds import add_server_parsers
from loom.config import AdminConfig


def _populate(subparsers) -> None:
    add_config_parsers(subparsers)
    add_db_parsers(subparsers)
    add_idp_parsers(subparsers)
    add_operator_parsers(subparsers)
    add_server_parsers(subparsers)


def run() -> int:
    return run_cli(
        f'Loom Admin {__version__}',
        default_admin_config_path(),
        AdminConfig,
        _populate,
        'LOOM_ADMIN_CONFIG_PATH',
    )


if __name__ == '__main__':
    sys.exit(run())
