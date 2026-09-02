import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from loom.config.database_config import DatabaseConfig
from loom.persistence.engine import get_async_engine, get_async_session_factory


def test_get_async_engine_builds_lazily_without_connecting():
    engine = get_async_engine(DatabaseConfig(host='unreachable-host'))
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == 'postgresql+psycopg'


@pytest.mark.asyncio
async def test_get_async_session_factory_can_query_sqlite(monkeypatch):
    monkeypatch.setattr(
        DatabaseConfig, 'dsn', property(lambda self: 'sqlite+aiosqlite:///:memory:')
    )
    config = DatabaseConfig()
    factory = get_async_session_factory(config)
    async with factory() as session:
        result = await session.execute(sa.text('SELECT 1'))
        assert result.scalar_one() == 1
    await factory.kw['bind'].dispose()
