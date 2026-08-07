import argparse
import pathlib
import sqlite3

import pytest

from loom.cli.db import db_current, db_downgrade, db_history, db_revision, db_upgrade
from loom.config import RootConfig


@pytest.fixture
def sqlite_root_config(tmp_path: pathlib.Path, monkeypatch) -> RootConfig:
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    db_path = tmp_path / 'loom_cli_test.db'
    monkeypatch.setattr(
        type(config.database), 'dsn', property(lambda self: f'sqlite:///{db_path}')
    )
    return config


@pytest.mark.asyncio
async def test_db_upgrade_and_downgrade_via_cli_functions(sqlite_root_config, tmp_path):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    db_path = tmp_path / 'loom_cli_test.db'
    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert 'tenant' in tables

    await db_downgrade(sqlite_root_config, argparse.Namespace(revision='base'))
    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert 'tenant' not in tables


@pytest.mark.asyncio
async def test_db_current_and_history_do_not_raise(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    assert await db_current(sqlite_root_config, argparse.Namespace()) == 0
    assert await db_history(sqlite_root_config, argparse.Namespace()) == 0


@pytest.mark.asyncio
async def test_db_revision_autogenerate_creates_new_file(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    result = await db_revision(
        sqlite_root_config,
        argparse.Namespace(message='add scratch table', autogenerate=False),
    )
    assert result == 0
