import argparse
import importlib.resources

from alembic import command
from alembic.config import Config

from loom.config import RootConfig


def _alembic_config(root_config: RootConfig) -> Config:
    """Build an Alembic Config for the packaged migrations and configured DSN."""
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', root_config.database.dsn)
    return config


async def db_upgrade(config: RootConfig, args: argparse.Namespace) -> int:
    command.upgrade(_alembic_config(config), getattr(args, 'revision', None) or 'head')
    return 0


async def db_downgrade(config: RootConfig, args: argparse.Namespace) -> int:
    command.downgrade(_alembic_config(config), args.revision)
    return 0


async def db_current(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    command.current(_alembic_config(config))
    return 0


async def db_history(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    command.history(_alembic_config(config))
    return 0


async def db_revision(config: RootConfig, args: argparse.Namespace) -> int:
    command.revision(
        _alembic_config(config), message=args.message, autogenerate=args.autogenerate
    )
    return 0
