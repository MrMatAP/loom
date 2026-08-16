import argparse
import asyncio
import enum
import pathlib
import sys
import typing
import uuid

import pydantic
import rich.console
import yaml

from loom import __version__, default_config_path
from loom.cli.auth import (
    auth_login,
    auth_logout,
    auth_set_tenant,
    auth_status,
    auth_whoami,
)
from loom.cli.catalog import add_catalog_parsers
from loom.cli.db import (
    db_current,
    db_downgrade,
    db_history,
    db_revision,
    db_upgrade,
)
from loom.cli.idp import idp_register, idp_unregister
from loom.config import RootConfig

console = rich.console.Console()


async def config_list(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    console.print(yaml.dump(config.model_dump(mode='json')))
    return 0


async def config_get(config: RootConfig, args: argparse.Namespace) -> int:
    """
    Print the value of a configuration value in the configuration hierarchy.
    Args:
        config (RootConfig): The root configuration object
        args (): The parsed command line arguments

    Returns:
        An integer exit code
    """
    if 'key' not in args:
        return await config_list(config, args)
    path = args.key.split('.')
    value = config.model_dump(mode='json', exclude_unset=True)
    for p in path:
        value = value[p]
    console.print(yaml.dump(value))
    return 0


async def config_set(config: RootConfig, args: argparse.Namespace) -> int:
    """
    Set a configuration value in the configuration hierarchy.
    Args:
        config (RootConfig): The root configuration object
        args (): The parsed command line arguments

    Returns:
        An integer exit code
    """
    if 'key' not in args or 'value' not in args:
        console.print('Please specify both a key and a value')
        return 1
    path = args.key.split('.')
    parent = config
    leaf = path[-1]
    current_path: list[str] = []
    for container in path[:-1]:
        current_path.append(container)
        if not hasattr(parent, container):
            console.print(f'There is no attribute at {".".join(current_path)}')
            return 1
        parent = getattr(parent, container)
    if not hasattr(parent, leaf):
        console.print(f'There is no attribute at {".".join(current_path + [leaf])}')
        return 1
    if issubclass(type(getattr(parent, leaf)), pydantic.BaseModel):
        console.print(
            'You cannot set the value of an entire object. Set a path that resolves to an attribute instead.'
        )
        return 1
    if issubclass(type(getattr(parent, leaf)), enum.Enum):
        if args.value not in list(type(getattr(parent, leaf))):
            console.print(
                f'The value {args.value} is not a valid option for {args.key}'
            )
            return 1
        else:
            setattr(parent, leaf, type(getattr(parent, leaf))(args.value))
    elif isinstance(getattr(parent, leaf), bool):
        setattr(parent, leaf, args.value.lower() == 'true')
    elif _is_secret_str_field(type(parent), leaf):
        setattr(parent, leaf, pydantic.SecretStr(args.value))
    else:
        setattr(parent, leaf, args.value)
    config.save()
    return 0


def _is_secret_str_field(model: type[pydantic.BaseModel], field_name: str) -> bool:
    """Whether the field is typed `SecretStr` (optionally `SecretStr | None`)."""
    annotation = model.model_fields[field_name].annotation
    if annotation is pydantic.SecretStr:
        return True
    return pydantic.SecretStr in typing.get_args(annotation)


def _add_admin_login_args(parser: argparse.ArgumentParser) -> None:
    """Flags shared by every `idp register-*` command's admin login."""
    parser.add_argument(
        '--issuer-url',
        dest='issuer_url',
        default=None,
        help='OIDC realm issuer URL, defaults to config.auth.issuer',
    )
    parser.add_argument(
        '--admin-username',
        dest='admin_username',
        default=None,
        help='Keycloak admin username, else LOOM_IDP_ADMIN_USERNAME or a prompt',
    )
    parser.add_argument(
        '--admin-password',
        dest='admin_password',
        default=None,
        help='Keycloak admin password, else LOOM_IDP_ADMIN_PASSWORD or a prompt',
    )
    parser.add_argument(
        '--admin-realm',
        dest='admin_realm',
        default='master',
        help='Realm to authenticate the admin user against',
    )
    parser.add_argument(
        '--admin-client-id',
        dest='admin_client_id',
        default='admin-cli',
        help='Public client used for the admin login grant',
    )


def _set_nested_value(target: dict, path: list[str], value: typing.Any) -> None:
    current = target
    for part in path[:-1]:
        current = current.setdefault(part, {})
    current[path[-1]] = value


def _collect_overrides(args: argparse.Namespace) -> dict:
    overrides = {}
    for key, value in vars(args).items():
        if not key.startswith('override_') or value is None:
            continue

        _set_nested_value(
            overrides,
            key.removeprefix('override_').split('__'),
            value,
        )

    return overrides


async def main() -> int:
    try:
        parser = argparse.ArgumentParser(f'Loom {__version__}')
        parser.add_argument(
            '--config',
            type=pathlib.Path,
            required=False,
            dest='config_path',
            default=default_config_path(),
            help=f'Path to the config file, defaults to {default_config_path()} '
            '(or $LOOM_CONFIG_PATH if set)',
        )
        parser.add_argument(
            '--verbose',
            '-v',
            action='store_true',
            default=False,
            required=False,
            dest='verbose',
            help='Enable verbose output',
        )
        subparsers = parser.add_subparsers(required=True, help='Sub-commands')
        config_parser = subparsers.add_parser('config', help='Configuration commands')
        config_subparser = config_parser.add_subparsers(required=True)
        config_list_parser = config_subparser.add_parser(
            'list', help='List current configuration'
        )
        config_list_parser.set_defaults(func=config_list)
        config_get_parser = config_subparser.add_parser(
            'get', help='Get a configuration value'
        )
        config_get_parser.add_argument('key', help='Setting key')
        config_get_parser.set_defaults(func=config_get)
        config_set_parser = config_subparser.add_parser(
            'set', help='Set a configuration value'
        )
        config_set_parser.add_argument('key', help='Setting key')
        config_set_parser.add_argument('value', help='Value to set for the key')
        config_set_parser.set_defaults(func=config_set)

        db_parser = subparsers.add_parser('db', help='Database migration commands')
        db_subparser = db_parser.add_subparsers(required=True)

        db_upgrade_parser = db_subparser.add_parser(
            'upgrade', help='Upgrade the database to a revision'
        )
        db_upgrade_parser.add_argument(
            'revision',
            nargs='?',
            default='head',
            help='Target revision, defaults to head',
        )
        db_upgrade_parser.set_defaults(func=db_upgrade)

        db_downgrade_parser = db_subparser.add_parser(
            'downgrade', help='Downgrade the database to a revision'
        )
        db_downgrade_parser.add_argument('revision', help='Target revision')
        db_downgrade_parser.set_defaults(func=db_downgrade)

        db_current_parser = db_subparser.add_parser(
            'current', help='Show the current database revision'
        )
        db_current_parser.set_defaults(func=db_current)

        db_history_parser = db_subparser.add_parser(
            'history', help='Show migration history'
        )
        db_history_parser.set_defaults(func=db_history)

        db_revision_parser = db_subparser.add_parser(
            'revision', help='Create a new migration revision'
        )
        db_revision_parser.add_argument(
            '-m', '--message', required=True, help='Revision message'
        )
        db_revision_parser.add_argument(
            '--autogenerate',
            action='store_true',
            default=False,
            help='Autogenerate from model changes',
        )
        db_revision_parser.set_defaults(func=db_revision)

        idp_parser = subparsers.add_parser(
            'idp', help='Identity provider bootstrap commands'
        )
        idp_subparser = idp_parser.add_subparsers(required=True)

        idp_register_parser = idp_subparser.add_parser(
            'register',
            help=(
                'Register all four Catalog OAuth clients (RESTful API, MCP '
                'server, Swagger UI, CLI) and declare the role vocabulary, '
                'in one run'
            ),
        )
        _add_admin_login_args(idp_register_parser)
        idp_register_parser.add_argument(
            '--client-id',
            dest='client_id',
            required=True,
            help='OAuth client ID to register for the RESTful API',
        )
        idp_register_parser.add_argument(
            '--client-name',
            dest='client_name',
            default=None,
            help="Human-readable API client name, defaults to 'Loom :: REST'",
        )
        idp_register_parser.add_argument(
            '--api-base-url',
            dest='api_base_url',
            required=True,
            help=(
                'Public base URL the Catalog API is served from, e.g. '
                'https://catalog.example.com (Swagger UI redirect URI is '
                '{api-base-url}/docs/oauth2-redirect)'
            ),
        )
        idp_register_parser.add_argument(
            '--mcp-client-id',
            dest='mcp_client_id',
            default=None,
            help='OAuth client ID for the MCP server, defaults to {client-id}-mcp',
        )
        idp_register_parser.add_argument(
            '--mcp-client-name',
            dest='mcp_client_name',
            default=None,
            help="Human-readable MCP client name, defaults to 'Loom :: MCP'",
        )
        idp_register_parser.add_argument(
            '--swagger-client-id',
            dest='swagger_client_id',
            default=None,
            help=('OAuth client ID for Swagger UI, defaults to {client-id}-swagger'),
        )
        idp_register_parser.add_argument(
            '--swagger-client-name',
            dest='swagger_client_name',
            default=None,
            help="Human-readable Swagger UI client name, defaults to 'Loom :: Swagger UI'",
        )
        idp_register_parser.add_argument(
            '--cli-client-id',
            dest='cli_client_id',
            default=None,
            help='OAuth client ID for the CLI, defaults to {client-id}-cli',
        )
        idp_register_parser.add_argument(
            '--cli-client-name',
            dest='cli_client_name',
            default=None,
            help="Human-readable CLI client name, defaults to 'Loom :: CLI'",
        )
        idp_register_parser.add_argument(
            '--access-token-lifespan',
            dest='access_token_lifespan',
            type=int,
            default=None,
            help=(
                "Override the CLI client's access-token lifespan in seconds "
                '(else LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN, else the realm '
                'default applies unmodified). Safe to re-run against an '
                'already-registered client to change it later.'
            ),
        )
        idp_register_parser.set_defaults(func=idp_register)

        idp_unregister_parser = idp_subparser.add_parser(
            'unregister',
            help=(
                'Delete the four Catalog OAuth clients `register` created '
                '(RESTful API, MCP server, Swagger UI, CLI) and clear the '
                'local config fields it set'
            ),
        )
        _add_admin_login_args(idp_unregister_parser)
        idp_unregister_parser.add_argument(
            '--client-id',
            dest='client_id',
            default=None,
            help=(
                'OAuth client ID for the RESTful API, defaults to config.auth.audience'
            ),
        )
        idp_unregister_parser.add_argument(
            '--mcp-client-id',
            dest='mcp_client_id',
            default=None,
            help=(
                'OAuth client ID for the MCP server, defaults to '
                'config.auth.mcp_audience, else {client-id}-mcp'
            ),
        )
        idp_unregister_parser.add_argument(
            '--swagger-client-id',
            dest='swagger_client_id',
            default=None,
            help=(
                'OAuth client ID for Swagger UI, defaults to '
                'config.auth.swagger_client_id, else {client-id}-swagger'
            ),
        )
        idp_unregister_parser.add_argument(
            '--cli-client-id',
            dest='cli_client_id',
            default=None,
            help=(
                'OAuth client ID for the CLI, defaults to '
                'config.auth.cli_client_id, else {client-id}-cli'
            ),
        )
        idp_unregister_parser.set_defaults(func=idp_unregister)

        auth_parser = subparsers.add_parser('auth', help='CLI login/session commands')
        auth_subparser = auth_parser.add_subparsers(required=True)

        auth_login_parser = auth_subparser.add_parser(
            'login', help="Log in interactively via the IDP's device-code flow"
        )
        auth_login_parser.add_argument(
            '--discovery-url',
            dest='discovery_url',
            default=None,
            help=('OIDC discovery document URL, defaults to config.auth.discovery_url'),
        )
        auth_login_parser.add_argument(
            '--client-id',
            dest='client_id',
            default=None,
            help=(
                'Public device-flow OAuth client ID, defaults to '
                'config.auth.cli_client_id'
            ),
        )
        auth_login_parser.set_defaults(func=auth_login)

        auth_logout_parser = auth_subparser.add_parser(
            'logout', help='Clear the cached CLI session'
        )
        auth_logout_parser.set_defaults(func=auth_logout)

        auth_status_parser = auth_subparser.add_parser(
            'status', help='Show the current CLI session status'
        )
        auth_status_parser.set_defaults(func=auth_status)

        auth_whoami_parser = auth_subparser.add_parser(
            'whoami',
            help=(
                "Show the cached access token's claims (sub, name, "
                'scopes, ...) -- for diagnosing 401/403s, not just whether '
                'the session is live'
            ),
        )
        auth_whoami_parser.set_defaults(func=auth_whoami)

        auth_set_tenant_parser = auth_subparser.add_parser(
            'set-tenant',
            help=(
                'Set/clear/show the locally-selected Tenant -- only needed '
                'when this identity is provisioned in more than one Tenant '
                '(see docs/admin-guide.md)'
            ),
        )
        auth_set_tenant_parser.add_argument(
            'tenant_id',
            type=uuid.UUID,
            nargs='?',
            default=None,
            help='Omit to show the current selection',
        )
        auth_set_tenant_parser.add_argument(
            '--clear',
            action='store_true',
            default=False,
            help='Clear the current selection instead of setting one',
        )
        auth_set_tenant_parser.set_defaults(func=auth_set_tenant)

        add_catalog_parsers(subparsers)

        args = parser.parse_args()
        config = RootConfig.load(config_path=args.config_path)
        config.save()
        return await args.func(config, args)
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # noqa: BLE001
        print(e)
    return 1


def run() -> int:
    return asyncio.run(main())


if __name__ == '__main__':
    sys.exit(run())
