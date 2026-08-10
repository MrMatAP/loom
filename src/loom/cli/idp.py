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


async def _login_as_admin(
    issuer_url: str, args: argparse.Namespace
) -> KeycloakAdminClient:
    """Resolve admin credentials and log in to the Keycloak Admin API."""
    username = _resolve_admin_username(args)
    password = _resolve_admin_password(args)
    return await KeycloakAdminClient.login(
        issuer_url,
        username=username,
        password=password,
        admin_realm=args.admin_realm,
        admin_client_id=args.admin_client_id,
    )


async def idp_register_client(config: RootConfig, args: argparse.Namespace) -> int:
    """Register the Catalog OAuth client and declare its role vocabulary."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1

    client = await _login_as_admin(issuer_url, args)

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


async def _grant_resource_server_claims(
    client: KeycloakAdminClient, internal_ref: str, config: RootConfig
) -> None:
    """Make tokens from a new public client pass the resource server's
    audience check, carry roles in the shape `expand_claims_to_scopes` reads,
    and carry a `tenant_id` claim -- all three are needed, or requests
    401/403 after a successful login."""
    await client.add_audience_mapper(
        internal_ref, target_client_id=config.auth.audience
    )
    await client.add_client_roles_mapper(
        internal_ref, source_client_id=config.auth.audience
    )
    await client.add_tenant_id_mapper(internal_ref)


def _require_resource_server_audience(config: RootConfig) -> bool:
    """Both public-client registrations need an existing resource-server
    client to grant an audience mapper against."""
    if config.auth.audience:
        return True
    print(
        'config.auth.audience is unset; run `loom idp register-client` first so '
        'there is a resource-server client to grant this client an audience '
        'mapper against.'
    )
    return False


async def idp_register_docs_client(config: RootConfig, args: argparse.Namespace) -> int:
    """Register the public Swagger UI client (Authorization Code + PKCE)."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1
    if not _require_resource_server_audience(config):
        return 1

    client = await _login_as_admin(issuer_url, args)

    client_name = args.client_name or args.client_id
    api_base_url = args.api_base_url.rstrip('/')
    redirect_uri = f'{api_base_url}/docs/oauth2-redirect'
    result = await client.register_public_client(
        client_id=args.client_id,
        client_name=client_name,
        standard_flow=True,
        redirect_uris=(redirect_uri,),
        web_origins=(api_base_url,),
    )
    await _grant_resource_server_claims(client, result.internal_ref, config)

    print(
        f'Registered docs client: {result.client_id} '
        f'(internal ref: {result.internal_ref})'
    )
    print(f'Redirect URI: {redirect_uri}')

    config.auth.docs_client_id = result.client_id
    config.save()
    print(f'Updated local config: auth.docs_client_id={result.client_id}')
    return 0


async def idp_register_cli_client(config: RootConfig, args: argparse.Namespace) -> int:
    """Register the public device-flow client used by `loom auth login`."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1
    if not _require_resource_server_audience(config):
        return 1

    client = await _login_as_admin(issuer_url, args)

    client_name = args.client_name or args.client_id
    result = await client.register_public_client(
        client_id=args.client_id, client_name=client_name, device_flow=True
    )
    await _grant_resource_server_claims(client, result.internal_ref, config)

    print(
        f'Registered CLI device-flow client: {result.client_id} '
        f'(internal ref: {result.internal_ref})'
    )

    config.auth.cli_client_id = result.client_id
    config.save()
    print(f'Updated local config: auth.cli_client_id={result.client_id}')
    return 0
