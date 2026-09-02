from collections.abc import AsyncGenerator

import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from loom.api.catalog.dependencies import (
    get_current_principal,
    get_current_token,
    get_session,
)
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.domain.enums import PrincipalKind
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.persistence import (
    agent,  # noqa: F401
    capability,  # noqa: F401
    dataproduct,  # noqa: F401
    datasource,  # noqa: F401
    environment,  # noqa: F401
    evaluation,  # noqa: F401
    governance,  # noqa: F401
    model_endpoint,  # noqa: F401
    observability,  # noqa: F401
    skill,  # noqa: F401
    tool,  # noqa: F401
)
from loom.persistence.base import Base
from loom.persistence.tenant import Principal, Tenant

ALL_SCOPES = content_scopes() | platform_scopes()


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @sa.event.listens_for(engine.sync_engine, 'connect')
    def _enable_fk(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session_factory(async_engine):
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def async_session(async_session_factory) -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def fake_principal(async_session_factory) -> AuthenticatedPrincipal:
    """Real Tenant+Principal seeded in the DB, wrapped with every scope granted."""
    async with async_session_factory() as session:
        tenant = Tenant(slug='test-tenant', name='Test Tenant')
        session.add(tenant)
        await session.flush()
        principal = Principal(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            external_id='test-user',
        )
        session.add(principal)
        await session.commit()
        return AuthenticatedPrincipal(
            principal_id=principal.id, tenant_id=tenant.id, scopes=frozenset(ALL_SCOPES)
        )


@pytest_asyncio.fixture
async def api_client(
    async_session_factory, fake_principal
) -> AsyncGenerator[AsyncClient]:
    from loom.api.catalog.main import create_app
    from loom.config import RootConfig

    app = create_app(RootConfig(config_path='/dev/null'))

    async def _override_session() -> AsyncGenerator[AsyncSession]:
        async with async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'test-user',
        'scope': ' '.join(sorted(ALL_SCOPES)),
    }
    app.dependency_overrides[get_current_principal] = lambda: fake_principal

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        client.app = app  # exposes the app so tests can re-override a dependency
        yield client
