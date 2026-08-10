import os
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

from loom.idp.keycloak import KeycloakAdminClient
from loom.tls import build_ssl_context

from .live import (
    ADMIN_PASSWORD_ENV_VAR,
    ADMIN_USERNAME_ENV_VAR,
    ISSUER_ENV_VAR,
    IT_PREFIX,
    admin_headers,
)


@pytest.fixture(scope='session')
def live_issuer() -> str:
    return os.environ[ISSUER_ENV_VAR].rstrip('/')


@pytest.fixture(scope='session')
def live_discovery_document(live_issuer: str) -> dict:
    """The issuer's real OIDC discovery document, fetched once per session:
    every test in this suite either depends on it directly or depends on a
    fixture that does."""
    ctx = build_ssl_context()
    response = httpx.get(
        f'{live_issuer}/.well-known/openid-configuration', timeout=10.0, verify=ctx
    )
    response.raise_for_status()
    return response.json()


@pytest_asyncio.fixture(scope='session')
async def admin_client(live_issuer: str) -> AsyncGenerator[KeycloakAdminClient]:
    """A logged-in Admin API client -- or a clean skip, distinct from any
    other failure mode, if Keycloak rejects the given admin credentials.
    A bad `.env` should read as "fix your credentials", not a test bug."""
    try:
        client = await KeycloakAdminClient.login(
            live_issuer,
            username=os.environ[ADMIN_USERNAME_ENV_VAR],
            password=os.environ[ADMIN_PASSWORD_ENV_VAR],
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == httpx.codes.UNAUTHORIZED:
            pytest.skip(
                f'IdP rejected the admin credentials in {ADMIN_USERNAME_ENV_VAR}/'
                f'{ADMIN_PASSWORD_ENV_VAR}: {exc.response.text.strip()}'
            )
        raise
    yield client


@pytest_asyncio.fixture(scope='session')
async def tenant_id_user_attribute(admin_client: KeycloakAdminClient) -> None:
    """Ensure the realm's declarative User Profile recognizes `tenant_id`
    as a user attribute.

    Keycloak's declarative User Profile silently drops any attribute not
    declared in its schema on user create/update -- `attributes` in the
    request body is accepted, but the server just omits it from the
    stored user rather than erroring, so `add_tenant_id_mapper`'s claim
    ends up empty with no signal pointing back at the cause. This is
    additive realm schema, not a throwaway test object (unlike everything
    else this suite tears down under `IT_PREFIX`), so it's left in place
    rather than reverted -- idempotent to re-run, and safe for the realm
    going forward."""
    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        headers = admin_headers(admin_client)
        profile_url = f'{admin_client._realm_admin_base}/users/profile'
        response = await http.get(profile_url, headers=headers, timeout=30.0)
        response.raise_for_status()
        profile = response.json()

        if any(attr['name'] == 'tenant_id' for attr in profile['attributes']):
            return

        profile['attributes'].append(
            {
                'name': 'tenant_id',
                'displayName': 'Tenant ID',
                'permissions': {'view': ['admin', 'user'], 'edit': ['admin', 'user']},
                'multivalued': False,
            }
        )
        update_response = await http.put(
            profile_url, json=profile, headers=headers, timeout=30.0
        )
        update_response.raise_for_status()


async def _delete_client(admin_client: KeycloakAdminClient, internal_ref: str) -> None:
    """Best-effort teardown: a client this suite created is gone either
    way, so a delete failure shouldn't fail the test that already ran."""
    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        await http.delete(
            f'{admin_client._realm_admin_base}/clients/{internal_ref}',
            headers=admin_headers(admin_client),
            timeout=30.0,
        )


@pytest_asyncio.fixture(scope='session')
async def resource_server_client(
    admin_client: KeycloakAdminClient,
) -> AsyncGenerator[tuple[str, str]]:
    """The confidential resource-server client, mirroring `loom idp
    register-client`: audience/roles mappers on the public clients below
    point back at this one."""
    from loom.idp.client import catalog_role_definitions

    client_id = f'{IT_PREFIX}-resource-server'
    result = await admin_client.register_client(
        client_id=client_id,
        client_name='Loom Integration Test Resource Server',
        service_account=True,
    )
    await admin_client.declare_client_roles(
        result.internal_ref, catalog_role_definitions()
    )
    yield client_id, result.internal_ref
    await _delete_client(admin_client, result.internal_ref)


@pytest_asyncio.fixture(scope='session')
async def docs_client(
    admin_client: KeycloakAdminClient, resource_server_client: tuple[str, str]
) -> AsyncGenerator[tuple[str, str]]:
    """The public Swagger UI client, mirroring `loom idp
    register-docs-client`: PKCE-only Authorization Code flow."""
    resource_server_id, _ = resource_server_client
    client_id = f'{IT_PREFIX}-docs'
    result = await admin_client.register_public_client(
        client_id=client_id,
        client_name='Loom Integration Test Docs Client',
        standard_flow=True,
        redirect_uris=('https://loom-it.invalid/docs/oauth2-redirect',),
        web_origins=('https://loom-it.invalid',),
    )
    await admin_client.add_audience_mapper(
        result.internal_ref, target_client_id=resource_server_id
    )
    await admin_client.add_client_roles_mapper(
        result.internal_ref, source_client_id=resource_server_id
    )
    await admin_client.add_tenant_id_mapper(result.internal_ref)
    yield client_id, result.internal_ref
    await _delete_client(admin_client, result.internal_ref)


@pytest_asyncio.fixture(scope='session')
async def cli_client(
    admin_client: KeycloakAdminClient, resource_server_client: tuple[str, str]
) -> AsyncGenerator[tuple[str, str]]:
    """The public `loom auth login` client, mirroring `loom idp
    register-cli-client`: device-authorization-grant only."""
    resource_server_id, _ = resource_server_client
    client_id = f'{IT_PREFIX}-cli'
    result = await admin_client.register_public_client(
        client_id=client_id,
        client_name='Loom Integration Test CLI Client',
        device_flow=True,
    )
    await admin_client.add_audience_mapper(
        result.internal_ref, target_client_id=resource_server_id
    )
    await admin_client.add_client_roles_mapper(
        result.internal_ref, source_client_id=resource_server_id
    )
    await admin_client.add_tenant_id_mapper(result.internal_ref)
    yield client_id, result.internal_ref
    await _delete_client(admin_client, result.internal_ref)
