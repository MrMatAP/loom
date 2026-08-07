import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from loom.config.database_config import DatabaseConfig


def get_engine(config: DatabaseConfig) -> sa.Engine:
    """Build a SQLAlchemy engine from database configuration; lazy, no connect."""
    return sa.create_engine(config.dsn)


def get_session_factory(config: DatabaseConfig) -> sessionmaker[Session]:
    """Build a session factory bound to an engine built from database configuration."""
    return sessionmaker(bind=get_engine(config))
