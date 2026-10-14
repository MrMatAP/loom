"""`loom-admin api` / `loom-admin mcp`: per-server provisioning. Each
generates that server's configuration file from what `idp register`
recorded, and creates the dedicated database role it connects as.

The database stays owned by the admin connection (full DDL, it runs the
migrations); these roles are DML-only -- see `loom.persistence.roles`."""

import argparse
import os
import pathlib
import secrets

from pydantic import SecretStr

from loom import default_api_config_path, default_mcp_config_path
from loom.config import AdminConfig, ApiServerConfig, McpServerConfig
from loom.persistence.roles import provision_role

DEFAULT_DB_USERNAMES = {'api': 'loom_api', 'mcp': 'loom_mcp'}


def _create_db_role(
    config: AdminConfig, server: str, args: argparse.Namespace
) -> tuple[str, str, bool]:
    """Create/refresh the server's DML-only role. Returns (username,
    password, generated) -- a password is generated when none was given."""
    username = args.db_username or DEFAULT_DB_USERNAMES[server]
    password = args.db_password or secrets.token_urlsafe(32)
    provision_role(config.database, username, password)
    return username, password, not args.db_password


async def _db_role(config: AdminConfig, args: argparse.Namespace, server: str) -> int:
    username, password, generated = _create_db_role(config, server, args)
    print(
        f"Role '{username}' can now SELECT/INSERT/UPDATE/DELETE on "
        f'{config.database.database} (no DDL).'
    )
    if generated:
        print('Generated password (store securely, shown once):')
        print(password)
    return 0


async def _from_env(config: AdminConfig, args: argparse.Namespace, server: str) -> int:
    """Write the server's config file from `LOOM_*` environment variables.
    Used by the container entrypoint; `config` (the admin config) is unused
    -- the servers' files are a separate trust domain."""
    del config, args
    if server == 'api':
        ApiServerConfig.from_env(default_api_config_path(), os.environ).save()
    else:
        McpServerConfig.from_env(default_mcp_config_path(), os.environ).save()
    return 0


async def _config(config: AdminConfig, args: argparse.Namespace, server: str) -> int:
    if server == 'api':
        path = pathlib.Path(args.output) if args.output else default_api_config_path()
        server_config = ApiServerConfig.from_admin(
            config, path, api_base_url=args.api_base_url
        )
    else:
        path = pathlib.Path(args.output) if args.output else default_mcp_config_path()
        server_config = McpServerConfig.from_admin(config, path)

    # Both given means the caller brings their own, already-provisioned
    # role: record it as-is rather than creating anything.
    supplied = bool(args.db_username and args.db_password)
    if supplied:
        username, password = args.db_username, args.db_password
    else:
        username, password, _ = _create_db_role(config, server, args)
    server_config.database.username = username
    server_config.database.password = SecretStr(password)
    server_config.save()

    print(f'Wrote {server} server config to {path}.')
    if supplied:
        print(f"Using the supplied database role '{username}' as-is (not created).")
    else:
        print(
            f"Created DML-only database role '{username}'; its credentials "
            'are in the config file.'
        )
    return 0


def _add_db_role_args(parser: argparse.ArgumentParser, server: str) -> None:
    parser.add_argument(
        '--db-username',
        '--db-user',
        dest='db_username',
        default=None,
        help=f'Database role name, defaults to {DEFAULT_DB_USERNAMES[server]}',
    )
    parser.add_argument(
        '--db-password',
        dest='db_password',
        default=None,
        help=(
            'Database role password, generated if unset. Re-running with an '
            'existing role sets its password again (rotation).'
        ),
    )


def _add_server_parser(subparsers: argparse._SubParsersAction, server: str) -> None:
    label = 'REST API' if server == 'api' else 'MCP server'
    parser = subparsers.add_parser(server, help=f'Provision the Catalog {label}')
    sub = parser.add_subparsers(required=True)

    config_parser = sub.add_parser(
        'config',
        help=(
            f"Generate the {label}'s configuration file from the admin config "
            'and create its DML-only database role, unless both --db-username '
            'and --db-password are given (then that role is used as-is)'
        ),
    )
    config_parser.add_argument(
        '--output',
        default=None,
        metavar='PATH',
        help=f'Where to write it, defaults to $LOOM_{server.upper()}_CONFIG_PATH',
    )
    if server == 'api':
        config_parser.add_argument(
            '--api-base-url',
            dest='api_base_url',
            default=None,
            help='Public base URL of the API, advertised to clients',
        )
    _add_db_role_args(config_parser, server)

    async def config_func(config: AdminConfig, args: argparse.Namespace) -> int:
        if server != 'api':
            args.api_base_url = None
        return await _config(config, args, server)

    config_parser.set_defaults(func=config_func)

    env_parser = sub.add_parser(
        'from-env',
        help=(
            f"Write the {label}'s config (at $LOOM_{server.upper()}_CONFIG_PATH) "
            'from LOOM_DB_*/LOOM_AUTH_* environment variables (container '
            'entrypoint)'
        ),
    )

    async def env_func(config: AdminConfig, args: argparse.Namespace) -> int:
        return await _from_env(config, args, server)

    env_parser.set_defaults(func=env_func)

    role_parser = sub.add_parser(
        'db-role',
        help=f"Create the {label}'s dedicated DML-only database role",
    )
    _add_db_role_args(role_parser, server)

    async def role_func(config: AdminConfig, args: argparse.Namespace) -> int:
        return await _db_role(config, args, server)

    role_parser.set_defaults(func=role_func)


def add_server_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register the `api` and `mcp` command groups."""
    _add_server_parser(subparsers, 'api')
    _add_server_parser(subparsers, 'mcp')
