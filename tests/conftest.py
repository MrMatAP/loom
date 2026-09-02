import sqlite3

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from loom.persistence.agent import Agent  # noqa: F401
from loom.persistence.base import Base
from loom.persistence.capability import Capability  # noqa: F401
from loom.persistence.dataproduct import (  # noqa: F401
    DataProduct,
    DataProductLineage,
)
from loom.persistence.datasource import DataSource  # noqa: F401
from loom.persistence.environment import Environment  # noqa: F401
from loom.persistence.model_endpoint import ModelEndpoint  # noqa: F401
from loom.persistence.observability import Metric  # noqa: F401
from loom.persistence.skill import (  # noqa: F401
    Skill,
    SkillGraphEdge,
    SkillGraphNode,
)
from loom.persistence.tenant import Principal, Tenant  # noqa: F401
from loom.persistence.tool import Tool, ToolDataBinding  # noqa: F401


@sa.event.listens_for(sa.engine.Engine, 'connect')
def _enable_sqlite_fk(dbapi_connection, connection_record) -> None:
    del connection_record
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()


@pytest.fixture
def engine() -> sa.Engine:
    test_engine = sa.create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    yield test_engine
    Base.metadata.drop_all(test_engine)


@pytest.fixture
def session(engine: sa.Engine):
    with Session(engine) as test_session:
        yield test_session
