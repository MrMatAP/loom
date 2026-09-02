import sqlalchemy as sa

from loom.config.database_config import DatabaseConfig
from loom.persistence.engine import get_engine, get_session_factory


def test_get_engine_builds_lazily_without_connecting():
    engine = get_engine(DatabaseConfig(host='unreachable-host'))
    assert isinstance(engine, sa.Engine)
    assert engine.url.drivername == 'postgresql+psycopg'


def test_get_session_factory_is_bound_to_engine():
    factory = get_session_factory(DatabaseConfig())
    assert factory.kw['bind'].url.database == 'loom'
