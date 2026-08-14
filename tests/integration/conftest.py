import importlib.resources
import os
from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from loom.config.database_config import DatabaseConfig
from loom.idp.keycloak import KeycloakAdminClient
from loom.model.engine import get_engine
from loom.tls import build_ssl_context

from .live import (
    ADMIN_PASSWORD_ENV_VAR,
    ADMIN_USERNAME_ENV_VAR,
    ISSUER_ENV_VAR,
    IT_PREFIX,
    admin_headers,
)
from .live_db import DB_HOST_ENV_VAR


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
    """The confidential RESTful API resource-server client, mirroring
    `loom idp register`'s API step: audience/roles mappers on the public
    clients below point back at this one -- the sole source of the
    declared role vocabulary (see `mcp_resource_server_client` below for
    why the MCP resource server doesn't get its own)."""
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
async def mcp_resource_server_client(
    admin_client: KeycloakAdminClient,
) -> AsyncGenerator[tuple[str, str]]:
    """The confidential MCP server resource-server client, mirroring `loom
    idp register`'s MCP step: a second value public-client tokens carry in
    `aud`, deliberately with no role declarations of its own (see
    `resource_server_client` above -- the vocabulary stays declared once)."""
    client_id = f'{IT_PREFIX}-mcp-resource-server'
    result = await admin_client.register_client(
        client_id=client_id,
        client_name='Loom Integration Test MCP Resource Server',
        service_account=True,
    )
    yield client_id, result.internal_ref
    await _delete_client(admin_client, result.internal_ref)


@pytest_asyncio.fixture(scope='session')
async def swagger_client(
    admin_client: KeycloakAdminClient,
    resource_server_client: tuple[str, str],
    mcp_resource_server_client: tuple[str, str],
) -> AsyncGenerator[tuple[str, str]]:
    """The public Swagger UI client, mirroring `loom idp register`'s
    Swagger UI step: PKCE-only Authorization Code flow, with an audience
    mapper for each resource server so its tokens satisfy both."""
    resource_server_id, _ = resource_server_client
    mcp_resource_server_id, _ = mcp_resource_server_client
    client_id = f'{IT_PREFIX}-swagger'
    result = await admin_client.register_public_client(
        client_id=client_id,
        client_name='Loom Integration Test Swagger UI Client',
        standard_flow=True,
        redirect_uris=('https://loom-it.invalid/docs/oauth2-redirect',),
        web_origins=('https://loom-it.invalid',),
    )
    await admin_client.add_audience_mapper(
        result.internal_ref, target_client_id=resource_server_id
    )
    await admin_client.add_audience_mapper(
        result.internal_ref, target_client_id=mcp_resource_server_id
    )
    await admin_client.add_client_roles_mapper(
        result.internal_ref, source_client_id=resource_server_id
    )
    yield client_id, result.internal_ref
    await _delete_client(admin_client, result.internal_ref)


@pytest_asyncio.fixture(scope='session')
async def cli_client(
    admin_client: KeycloakAdminClient,
    resource_server_client: tuple[str, str],
    mcp_resource_server_client: tuple[str, str],
) -> AsyncGenerator[tuple[str, str]]:
    """The public `loom auth login` client, mirroring `loom idp register`'s
    CLI step: device-authorization-grant only, with an audience mapper for
    each resource server so its tokens satisfy both."""
    resource_server_id, _ = resource_server_client
    mcp_resource_server_id, _ = mcp_resource_server_client
    client_id = f'{IT_PREFIX}-cli'
    result = await admin_client.register_public_client(
        client_id=client_id,
        client_name='Loom Integration Test CLI Client',
        device_flow=True,
    )
    await admin_client.add_audience_mapper(
        result.internal_ref, target_client_id=resource_server_id
    )
    await admin_client.add_audience_mapper(
        result.internal_ref, target_client_id=mcp_resource_server_id
    )
    await admin_client.add_client_roles_mapper(
        result.internal_ref, source_client_id=resource_server_id
    )
    yield client_id, result.internal_ref
    await _delete_client(admin_client, result.internal_ref)


@pytest.fixture(scope='session')
def live_db_config() -> DatabaseConfig:
    """DatabaseConfig built from the same LOOM_DB_* names entrypoint.sh/
    docs/admin-guide.md use -- deliberately not LOOM_IDP_ISSUER_*-shaped
    names, since this suite talks to Postgres, not Keycloak."""
    return DatabaseConfig(
        host=os.environ[DB_HOST_ENV_VAR],
        port=int(os.environ.get('LOOM_DB_PORT', '5432')),
        database=os.environ.get('LOOM_DB_NAME', 'loom'),
        username=os.environ.get('LOOM_DB_USERNAME', 'loom'),
        password=os.environ.get('LOOM_DB_PASSWORD'),
    )


@pytest.fixture(scope='session')
def live_db_engine(live_db_config: DatabaseConfig) -> Generator[Engine]:
    """A real Postgres engine, migrated to `head` via the packaged Alembic
    revisions -- the same path `loom db upgrade` runs in production, so this
    exercises the actual schema (native UUID/enum types, constraints) rather
    than the sqlite approximation the rest of the suite uses. Migrations are
    additive schema, not a throwaway test object, so they're left applied
    rather than downgraded afterward.

    A bad LOOM_DB_PASSWORD reads as a clean skip, distinct from any other
    failure mode -- same reasoning as `admin_client` above: a bad `.env`
    should read as "fix your credentials", not a test bug."""
    script_location = importlib.resources.files('loom') / 'migrations'
    alembic_config = Config()
    alembic_config.set_main_option('script_location', str(script_location))
    alembic_config.set_main_option('sqlalchemy.url', live_db_config.dsn)
    try:
        command.upgrade(alembic_config, 'head')
    except sa.exc.OperationalError as exc:
        cause = exc.orig
        if 'password authentication failed' in str(cause):
            pytest.skip(
                f'Postgres rejected the credentials for LOOM_DB_HOST='
                f'{live_db_config.host!r}, user {live_db_config.username!r}: {cause}'
            )
        raise

    engine = get_engine(live_db_config)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def live_db_session(live_db_engine: Engine) -> Generator[sa.orm.Session]:
    """A transactional session for one test -- rolled back on exit so
    nothing this suite writes (even outside the `IT_PREFIX` convention)
    persists in the shared database."""
    connection = live_db_engine.connect()
    transaction = connection.begin()
    session = sa.orm.Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
