"""Tier 3 (admin-gated): the centerpiece of this suite.

The protocol mappers `loom idp register`'s Swagger UI/CLI steps attach
(two audience mappers -- one per resource server -- plus client-roles; see
`KeycloakAdminClient.add_audience_mapper`/`add_client_roles_mapper`) have
been flagged as unverified against a real Keycloak across multiple prior
sessions: the mocked test suite can only assert *that a POST was sent*,
never that Keycloak actually honors it and issues a token shaped the way
`security.expand_claims_to_scopes` and `dependencies.get_current_principal`
expect.

This closes that gap end to end: real Keycloak-issued, real
signature-verified JWT -> `TokenValidator.decode()` -> `AuthenticatedPrincipal`
-> a real (sqlite-backed) FastAPI request answered by a scope-gated route.

Getting a real user token without a browser needs *some* grant Keycloak
will do headlessly. Swagger/CLI deliberately never use the Resource Owner
Password grant (`directAccessGrantsEnabled` is hardcoded False on both --
see `KeycloakAdminClient.register_client`/`register_public_client`), and
that choice is correct and must not change. So this suite creates its own
throwaway password-grant client purely to pull a real token to inspect --
it is never used to service an actual login and is deleted at the end of
the run, same as every other object this suite creates.
"""

import uuid
from collections.abc import AsyncGenerator

import httpx
import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from loom.api.catalog.dependencies import get_session
from loom.api.catalog.main import create_app
from loom.api.catalog.security import TokenValidator, expand_claims_to_scopes
from loom.config import RootConfig
from loom.domain.enums import PrincipalKind
from loom.idp.catalog_roles import ROLE_BUNDLES
from loom.idp.discovery import default_discovery_url, discover_oidc
from loom.persistence import agent  # noqa: F401 -- table registration side effect
from loom.persistence.base import Base
from loom.persistence.tenant import Principal, Tenant
from loom.tls import build_ssl_context

from .live import (
    ADMIN_PASSWORD_ENV_VAR,
    ADMIN_USERNAME_ENV_VAR,
    ISSUER_ENV_VAR,
    IT_PREFIX,
    admin_headers,
    requires_env_vars,
)

pytestmark = [
    pytest.mark.live_idp,
    requires_env_vars(ISSUER_ENV_VAR, ADMIN_USERNAME_ENV_VAR, ADMIN_PASSWORD_ENV_VAR),
]

_PROBE_ROLE = 'catalog-viewer'
_TEST_PASSWORD = 'loom-it-throwaway-P4ssword!'  # nosec: throwaway test-only account


@pytest_asyncio.fixture(scope='module')
async def probe_client(
    admin_client, resource_server_client, mcp_resource_server_client
) -> AsyncGenerator[tuple[str, str]]:
    """A password-grant-enabled public client, deliberately outside
    `KeycloakAdminClient`'s registration API (see module docstring): built
    with a raw Admin API call so the product's own client-creation methods
    never gain a `directAccessGrantsEnabled` toggle a future caller could
    misuse. Mapped against both resource servers, mirroring `swagger_client`/
    `cli_client` in conftest.py, so its tokens prove the dual-audience shape
    those clients rely on."""
    resource_server_id, _ = resource_server_client
    mcp_resource_server_id, _ = mcp_resource_server_client
    client_id = f'{IT_PREFIX}-claims-probe'
    payload = {
        'clientId': client_id,
        'name': 'Loom Integration Test Claims Probe (throwaway, ROPC)',
        'publicClient': True,
        'serviceAccountsEnabled': False,
        'standardFlowEnabled': False,
        'directAccessGrantsEnabled': True,
    }
    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        response = await http.post(
            f'{admin_client._realm_admin_base}/clients',
            json=payload,
            headers=admin_headers(admin_client),
            timeout=30.0,
        )
        response.raise_for_status()
        internal_ref = response.headers['Location'].rstrip('/').rsplit('/', 1)[-1]

    await admin_client.add_audience_mapper(
        internal_ref, target_client_id=resource_server_id
    )
    await admin_client.add_audience_mapper(
        internal_ref, target_client_id=mcp_resource_server_id
    )
    await admin_client.add_client_roles_mapper(
        internal_ref, source_client_id=resource_server_id
    )

    yield client_id, internal_ref

    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        await http.delete(
            f'{admin_client._realm_admin_base}/clients/{internal_ref}',
            headers=admin_headers(admin_client),
            timeout=30.0,
        )


@pytest_asyncio.fixture(scope='module')
async def test_user(admin_client, resource_server_client) -> AsyncGenerator[dict]:
    """A throwaway realm user granted the `catalog-viewer` client role, so
    a token issued to it exercises every mapper at once. No `tenant_id`
    account attribute -- a caller's Tenant is resolved from their
    `Principal` row (`Principal.tenant_id`), not a token claim; see
    docs/admin-guide.md's "Platform administrator" section."""
    resource_server_id, resource_server_ref = resource_server_client
    del resource_server_id
    username = f'{IT_PREFIX}-user-{uuid.uuid4().hex[:8]}'

    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        create_response = await http.post(
            f'{admin_client._realm_admin_base}/users',
            json={'username': username, 'enabled': True},
            headers=admin_headers(admin_client),
            timeout=30.0,
        )
        create_response.raise_for_status()
        user_id = create_response.headers['Location'].rstrip('/').rsplit('/', 1)[-1]

        await http.put(
            f'{admin_client._realm_admin_base}/users/{user_id}/reset-password',
            json={'type': 'password', 'value': _TEST_PASSWORD, 'temporary': False},
            headers=admin_headers(admin_client),
            timeout=30.0,
        )

        role_response = await http.get(
            f'{admin_client._realm_admin_base}/clients/{resource_server_ref}/roles/'
            f'{_PROBE_ROLE}',
            headers=admin_headers(admin_client),
            timeout=30.0,
        )
        role_response.raise_for_status()
        await http.post(
            f'{admin_client._realm_admin_base}/users/{user_id}/role-mappings/clients/'
            f'{resource_server_ref}',
            json=[role_response.json()],
            headers=admin_headers(admin_client),
            timeout=30.0,
        )

    yield {'username': username, 'user_id': user_id}

    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        await http.delete(
            f'{admin_client._realm_admin_base}/users/{user_id}',
            headers=admin_headers(admin_client),
            timeout=30.0,
        )


@pytest_asyncio.fixture(scope='module')
async def live_user_token(live_issuer, probe_client, test_user) -> str:
    """A real Keycloak-issued access token for `test_user`, obtained via
    `probe_client`'s throwaway password grant."""
    client_id, _ = probe_client
    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        response = await http.post(
            f'{live_issuer}/protocol/openid-connect/token',
            data={
                'grant_type': 'password',
                'client_id': client_id,
                'username': test_user['username'],
                'password': _TEST_PASSWORD,
            },
            timeout=30.0,
        )
    response.raise_for_status()
    return response.json()['access_token']


def test_live_token_carries_the_expected_audience_and_roles(
    live_user_token, resource_server_client, mcp_resource_server_client
):
    resource_server_id, _ = resource_server_client
    mcp_resource_server_id, _ = mcp_resource_server_client
    claims = jwt.decode(live_user_token, options={'verify_signature': False})

    # `aud` is a bare string when there's exactly one audience, a list
    # otherwise -- normalize before membership-testing so this can't pass
    # as an accidental substring match on the string form.
    audiences = claims['aud']
    if isinstance(audiences, str):
        audiences = [audiences]
    # Both resource servers, from the two `add_audience_mapper` calls in
    # `probe_client` -- proves `aud` stacks rather than the second mapper
    # clobbering the first (see `cli.idp._grant_resource_server_claims`).
    assert resource_server_id in audiences
    assert mcp_resource_server_id in audiences
    assert _PROBE_ROLE in claims['roles']

    scopes = expand_claims_to_scopes(claims)
    # `catalog-viewer` grants only the read scopes (see `ROLE_BUNDLES`) --
    # not `content_scopes()`, which also includes write/transition and is
    # what `catalog-admin` maps to.
    assert scopes >= ROLE_BUNDLES['catalog-viewer']
    assert 'catalog:agent:write' not in scopes  # ...but none of the write ones


def test_token_validator_verifies_the_live_signature(
    live_issuer, live_user_token, resource_server_client
):
    """The same `TokenValidator` the running API uses, pointed at this
    instance's real JWKS -- proves the signature verification path, not
    just claim shape (the previous test intentionally skips verification
    to isolate mapper output from signature validity)."""
    resource_server_id, _ = resource_server_client
    config = RootConfig(config_path='/dev/null')
    config.auth.issuer = live_issuer
    config.auth.audience = resource_server_id

    discovery = discover_oidc(default_discovery_url(live_issuer))
    validator = TokenValidator(config.auth, discovery)
    claims = validator.decode(live_user_token)

    assert claims['sub']


def test_token_validator_also_accepts_the_mcp_audience(
    live_issuer, live_user_token, mcp_resource_server_client
):
    """The MCP server builds its own `TokenValidator` against
    `auth.mcp_audience` (see `mcp.main.create_app`'s lifespan), not
    `auth.audience` -- proves the same token that satisfies the REST API's
    validator above also satisfies a validator scoped to the MCP resource
    server, via the second audience mapper on the public client."""
    mcp_resource_server_id, _ = mcp_resource_server_client
    config = RootConfig(config_path='/dev/null')
    config.auth.issuer = live_issuer
    config.auth.audience = mcp_resource_server_id

    discovery = discover_oidc(default_discovery_url(live_issuer))
    validator = TokenValidator(config.auth, discovery)
    claims = validator.decode(live_user_token)

    assert claims['sub']


@pytest.mark.asyncio
async def test_live_token_authorizes_a_real_api_request(
    live_issuer, live_user_token, resource_server_client, test_user
):
    """End to end: a real token, decoded by the real validator, resolved
    to a `Principal` seeded from the token's own `sub` (not a fixture
    constant -- Keycloak generates it, we don't get to choose it), used to
    answer a real scope-gated route. The Tenant this Principal belongs to
    is minted locally, not read off the token -- `Principal.tenant_id` is
    the sole source of that, not a claim; see docs/admin-guide.md's
    "Platform administrator" section."""
    del test_user
    resource_server_id, _ = resource_server_client
    sub = jwt.decode(live_user_token, options={'verify_signature': False})['sub']
    tenant_id = uuid.uuid4()

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Tenant(id=tenant_id, slug='loom-it', name='Loom IT Tenant'))
        await session.flush()
        session.add(
            Principal(
                tenant_id=tenant_id,
                kind=PrincipalKind.USER,
                external_id=sub,
            )
        )
        await session.commit()

    config = RootConfig(config_path='/dev/null')
    config.auth.issuer = live_issuer
    config.auth.audience = resource_server_id
    app = create_app(config)
    # Bypass the Postgres-backed lifespan entirely: we only need the real
    # TokenValidator (set directly below), not the app's default database.
    # A second discovery fetch (`create_app` above already did one via
    # `discover_and_resolve_issuer`) -- acceptable here, this is a live
    # integration test, not a latency-sensitive path.
    discovery = discover_oidc(default_discovery_url(live_issuer))
    app.state.token_validator = TokenValidator(config.auth, discovery)

    async def _override_session() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get(
            '/api/v1/agents',
            headers={'Authorization': f'Bearer {live_user_token}'},
        )

    assert response.status_code == 200
    assert response.json()['total'] == 0

    await engine.dispose()
