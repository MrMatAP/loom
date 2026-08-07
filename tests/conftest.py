import sqlite3

import pytest
import sqlalchemy as sa
from loom.model.base import Base
from sqlalchemy.orm import Session


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
