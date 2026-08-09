import argparse
import getpass
import os

from loom.config import RootConfig
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


def _resolve_admin_username(args: argparse.Namespace) -> str:
    """Resolve the admin username from flag, env var, or an interactive prompt."""
    if args.admin_username:
        return args.admin_username
    env_value = os.environ.get('LOOM_IDP_ADMIN_USERNAME')
    if env_value:
        return env_value
    return input('Keycloak admin username: ')


def _resolve_admin_password(args: argparse.Namespace) -> str:
    """Resolve the admin password from flag, env var, or a hidden prompt."""
    if args.admin_password:
        return args.admin_password
    env_value = os.environ.get('LOOM_IDP_ADMIN_PASSWORD')
    if env_value:
        return env_value
    return getpass.getpass('Keycloak admin password: ')


async def idp_register_client(config: RootConfig, args: argparse.Namespace) -> int:
    """Register the Catalog OAuth client and declare its role vocabulary."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1

    username = _resolve_admin_username(args)
    password = _resolve_admin_password(args)
    client = await KeycloakAdminClient.login(
        issuer_url,
        username=username,
        password=password,
        admin_realm=args.admin_realm,
        admin_client_id=args.admin_client_id,
    )

    client_name = args.client_name or args.client_id
    result = await client.register_client(
        client_id=args.client_id, client_name=client_name, service_account=True
    )

    roles = catalog_role_definitions()
    await client.declare_client_roles(result.internal_ref, roles)

    print(
        f'Registered client: {result.client_id} (internal ref: {result.internal_ref})'
    )
    if result.client_secret:
        print('Client secret (store securely, shown once):')
        print(result.client_secret)
    print(f'Declared {len(roles)} roles under the client.')

    config.auth.issuer = issuer_url
    config.auth.audience = result.client_id
    config.auth.jwks_uri = None
    config.save()
    print(
        f'Updated local config: auth.issuer={issuer_url}, '
        f'auth.audience={result.client_id} (auth.jwks_uri reset to re-derive)'
    )
    return 0
