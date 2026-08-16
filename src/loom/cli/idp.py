import argparse
import getpass
import os

from loom.config import RootConfig
from loom.idp.client import catalog_role_definitions
from loom.idp.discovery import default_discovery_url
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


def _resolve_access_token_lifespan(args: argparse.Namespace) -> int | None:
    """Resolve the CLI session's access-token lifespan override from flag or
    env var; None (Keycloak's realm default applies, unmodified) if neither
    is set. Keycloak realm defaults are commonly a few minutes -- short
    enough that `loom auth login` sessions can feel like they expire almost
    immediately, since `catalog.py`'s own auto-refresh isn't implemented
    yet (see README's CLI session notes)."""
    if args.access_token_lifespan is not None:
        return args.access_token_lifespan
    env_value = os.environ.get('LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN')
    return int(env_value) if env_value else None


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


async def _grant_resource_server_claims(
    client: KeycloakAdminClient,
    internal_ref: str,
    *,
    api_client_id: str,
    mcp_client_id: str,
) -> None:
    """Make tokens from a new public client pass both resource servers'
    audience checks and carry roles in the shape `expand_claims_to_scopes`
    reads -- both needed, or requests 401/403 after a successful login.
    `aud` is natively multivalued, so one audience mapper per resource
    server stacks rather than conflicts; the client-roles mapper only ever
    points at the API client, since the role vocabulary is declared once,
    not duplicated under the MCP client (see `_register_mcp_client`).
    Deliberately no `tenant_id` mapper: a caller's Tenant now comes from
    their `Principal` row (`Principal.tenant_id`), not a token claim -- see
    docs/admin-guide.md's "Platform administrator" section."""
    await client.add_audience_mapper(internal_ref, target_client_id=api_client_id)
    await client.add_audience_mapper(internal_ref, target_client_id=mcp_client_id)
    await client.add_client_roles_mapper(internal_ref, source_client_id=api_client_id)


async def _register_api_client(
    client: KeycloakAdminClient,
    config: RootConfig,
    issuer_url: str,
    args: argparse.Namespace,
) -> str:
    """Register the confidential RESTful API resource-server client and
    declare the shared scope/role vocabulary under it -- the single source
    of roles every public client's client-roles mapper reads from."""
    client_name = args.client_name or 'Loom :: REST'
    result = await client.register_client(
        client_id=args.client_id, client_name=client_name, service_account=True
    )

    roles = catalog_role_definitions()
    await client.declare_client_roles(result.internal_ref, roles)

    print(
        f'Registered API client: {result.client_id} '
        f'(internal ref: {result.internal_ref})'
    )
    if result.client_secret:
        print('Client secret (store securely, shown once):')
        print(result.client_secret)
    print(f'Declared {len(roles)} roles under the client.')

    config.auth.issuer = issuer_url
    config.auth.audience = result.client_id
    # `discovery_url` is the primary, stored value going forward (the
    # server re-derives `issuer` from it, and fails startup if a stored
    # `issuer` ever disagrees -- see `security.discover_and_resolve_
    # issuer`); populate it here too, rather than leaving it to whatever
    # was there before, so a freshly-registered environment starts
    # consistent instead of relying on the two ever being reconciled later.
    config.auth.discovery_url = default_discovery_url(issuer_url)
    config.save()
    print(
        f'Updated local config: auth.issuer={issuer_url}, '
        f'auth.audience={result.client_id}, '
        f'auth.discovery_url={config.auth.discovery_url}'
    )
    return result.client_id


async def _register_mcp_client(
    client: KeycloakAdminClient, config: RootConfig, args: argparse.Namespace
) -> str:
    """Register the confidential MCP server resource-server client --
    deliberately no role declarations here; see `_grant_resource_server_claims`
    for why the vocabulary stays declared once, under the API client."""
    mcp_client_id = args.mcp_client_id or f'{args.client_id}-mcp'
    client_name = args.mcp_client_name or 'Loom :: MCP'
    result = await client.register_client(
        client_id=mcp_client_id, client_name=client_name, service_account=True
    )

    print(
        f'Registered MCP client: {result.client_id} '
        f'(internal ref: {result.internal_ref})'
    )
    if result.client_secret:
        print('Client secret (store securely, shown once):')
        print(result.client_secret)

    config.auth.mcp_audience = result.client_id
    config.save()
    print(f'Updated local config: auth.mcp_audience={result.client_id}')
    return result.client_id


async def _register_swagger_client(
    client: KeycloakAdminClient,
    config: RootConfig,
    args: argparse.Namespace,
    api_client_id: str,
    mcp_client_id: str,
) -> None:
    """Register the public Swagger UI client (Authorization Code + PKCE)
    for interactive login from /docs."""
    swagger_client_id = args.swagger_client_id or f'{args.client_id}-swagger'
    client_name = args.swagger_client_name or 'Loom :: Swagger UI'
    api_base_url = args.api_base_url.rstrip('/')
    redirect_uri = f'{api_base_url}/docs/oauth2-redirect'
    result = await client.register_public_client(
        client_id=swagger_client_id,
        client_name=client_name,
        standard_flow=True,
        redirect_uris=(redirect_uri,),
        web_origins=(api_base_url,),
    )
    await _grant_resource_server_claims(
        client,
        result.internal_ref,
        api_client_id=api_client_id,
        mcp_client_id=mcp_client_id,
    )

    print(
        f'Registered Swagger UI client: {result.client_id} '
        f'(internal ref: {result.internal_ref})'
    )
    print(f'Redirect URI: {redirect_uri}')

    config.auth.swagger_client_id = result.client_id
    config.save()
    print(f'Updated local config: auth.swagger_client_id={result.client_id}')


async def _register_cli_client(
    client: KeycloakAdminClient,
    config: RootConfig,
    args: argparse.Namespace,
    api_client_id: str,
    mcp_client_id: str,
) -> None:
    """Register the public device-flow client used by `loom auth login`."""
    cli_client_id = args.cli_client_id or f'{args.client_id}-cli'
    client_name = args.cli_client_name or 'Loom :: CLI'
    result = await client.register_public_client(
        client_id=cli_client_id, client_name=client_name, device_flow=True
    )
    await _grant_resource_server_claims(
        client,
        result.internal_ref,
        api_client_id=api_client_id,
        mcp_client_id=mcp_client_id,
    )

    print(
        f'Registered CLI device-flow client: {result.client_id} '
        f'(internal ref: {result.internal_ref})'
    )

    lifespan = _resolve_access_token_lifespan(args)
    if lifespan is not None:
        await client.set_access_token_lifespan(result.internal_ref, lifespan)
        print(
            f'Set access-token lifespan to {lifespan}s (overrides the realm default).'
        )

    config.auth.cli_client_id = result.client_id
    config.save()
    print(f'Updated local config: auth.cli_client_id={result.client_id}')


async def idp_register(config: RootConfig, args: argparse.Namespace) -> int:
    """Register all four Catalog OAuth clients -- RESTful API, MCP server,
    Swagger UI, CLI -- against the IDP in one run, in dependency order: the
    two resource-server clients first (the public clients need both to
    exist so they can be granted audience mappers against them), then the
    two public interactive-login clients. Every step is idempotent-on-409
    (see `KeycloakAdminClient`), and `config` is saved after each
    successful step, so a run interrupted partway through is safe to just
    re-run in full."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1

    client = await _login_as_admin(issuer_url, args)

    api_client_id = await _register_api_client(client, config, issuer_url, args)
    mcp_client_id = await _register_mcp_client(client, config, args)
    await _register_swagger_client(client, config, args, api_client_id, mcp_client_id)
    await _register_cli_client(client, config, args, api_client_id, mcp_client_id)
    return 0


async def _delete_client(
    client: KeycloakAdminClient, label: str, client_id: str
) -> None:
    """Delete one client and print what happened. Idempotent -- "already
    gone" (e.g. a re-run, or a client `idp_register` never actually
    created) is a normal outcome, not a failure, same as a 409 is for the
    create path."""
    deleted = await client.delete_client(client_id=client_id)
    if deleted:
        print(f'Deleted {label} client: {client_id}')
    else:
        print(f'{label} client {client_id} was already absent, nothing to delete.')


async def idp_unregister(config: RootConfig, args: argparse.Namespace) -> int:
    """Delete the four Catalog OAuth clients `idp_register` created --
    RESTful API, MCP server, Swagger UI, CLI -- and clear the local config
    fields it set, undoing that command. Every deletion is idempotent (see
    `_delete_client`), so a run interrupted partway through -- or run
    against a config that was never fully registered -- is safe to just
    re-run in full, the same guarantee `idp_register` makes for creation.

    Client ids are resolved the same way `idp_register` derives them
    (`--client-id`/`--mcp-client-id`/etc.), but prefer whatever's actually
    stored in `config.auth` first, over re-deriving the
    `{client-id}-mcp`/`-swagger`/`-cli` naming convention -- in case a
    prior `register` run used custom ids that don't follow it. Roles and
    protocol mappers `register` attached to a client are Keycloak
    sub-resources of that client, so deleting the client deletes them too
    -- there's nothing left over to clean up separately.

    Config is cleared unconditionally once all four deletions have been
    attempted, even if some (or all) of them reported "already absent".
    That's deliberate, not a gap: an id resolved from config that Keycloak
    doesn't recognize means the config was already stale, and clearing it
    is the correct way to reflect that -- not a reason to leave it in
    place. What it does mean: if you pass the *wrong* `--client-id` for
    this environment, unregister will report the (unrelated) client as
    absent, still wipe local config, and the real client will be
    untouched in Keycloak under whatever id it actually has. Double-check
    `--issuer-url`/`--client-id` against the environment you mean to
    unregister before running this.

    Does not touch `config.auth.session` (the cached CLI login, if any) --
    the identity that login belongs to may still be usable against a
    different IDP setup; run `loom auth logout` separately if it isn't."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1

    api_client_id = args.client_id or config.auth.audience
    if not api_client_id:
        print(
            'No --client-id given and config.auth.audience is unset -- '
            'nothing to unregister.'
        )
        return 1
    mcp_client_id = (
        args.mcp_client_id or config.auth.mcp_audience or f'{api_client_id}-mcp'
    )
    swagger_client_id = (
        args.swagger_client_id
        or config.auth.swagger_client_id
        or f'{api_client_id}-swagger'
    )
    cli_client_id = (
        args.cli_client_id or config.auth.cli_client_id or f'{api_client_id}-cli'
    )

    client = await _login_as_admin(issuer_url, args)

    # Reverse of `idp_register`'s order -- Keycloak doesn't actually
    # enforce any dependency between these (each public client's protocol
    # mappers just reference the other's `clientId` string, no DB foreign
    # key), but it reads as the deletions undoing the creations in the
    # order they happened.
    await _delete_client(client, 'CLI device-flow', cli_client_id)
    await _delete_client(client, 'Swagger UI', swagger_client_id)
    await _delete_client(client, 'MCP', mcp_client_id)
    await _delete_client(client, 'API', api_client_id)

    config.auth.issuer = ''
    config.auth.audience = ''
    config.auth.discovery_url = None
    config.auth.mcp_audience = ''
    config.auth.swagger_client_id = ''
    config.auth.cli_client_id = ''
    config.save()
    print(
        'Cleared local config: auth.issuer, auth.audience, '
        'auth.discovery_url, auth.mcp_audience, auth.swagger_client_id, '
        'auth.cli_client_id.'
    )
    return 0
