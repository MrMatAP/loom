import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from loom.config.database_config import DatabaseConfig


def get_engine(config: DatabaseConfig) -> sa.Engine:
    """Build a SQLAlchemy engine from database configuration; lazy, no connect."""
    return sa.create_engine(config.dsn)


def get_session_factory(config: DatabaseConfig) -> sessionmaker[Session]:
    """Build a session factory bound to an engine built from database configuration."""
    return sessionmaker(bind=get_engine(config))


def get_async_engine(config: DatabaseConfig) -> AsyncEngine:
    """Build an async SQLAlchemy engine from database configuration."""
    return create_async_engine(config.dsn)


def get_async_session_factory(
    config: DatabaseConfig,
) -> async_sessionmaker[AsyncSession]:
    """Build an async session factory bound to an async engine from config."""
    return async_sessionmaker(bind=get_async_engine(config), expire_on_commit=False)
