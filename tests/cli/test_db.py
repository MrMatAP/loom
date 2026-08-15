import argparse
import importlib.resources
import pathlib
import sqlite3

import pytest
from alembic import command

from loom.cli.db import (
    _alembic_config,
    db_current,
    db_downgrade,
    db_history,
    db_revision,
    db_upgrade,
)
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
async def test_db_upgrade_seeds_a_default_tenant_on_an_empty_database(
    sqlite_root_config, tmp_path, capsys
):
    """Most deployments only ever need one Tenant -- `db upgrade` creates
    it so a platform admin doesn't need an explicit `loom tenant create`
    step for that common case (see docs/admin-guide.md's "Platform
    administrator" section)."""
    result = await db_upgrade(sqlite_root_config, argparse.Namespace())
    assert result == 0

    db_path = tmp_path / 'loom_cli_test.db'
    conn = sqlite3.connect(db_path)
    rows = conn.execute('SELECT slug, name FROM tenant').fetchall()
    conn.close()
    assert rows == [('default', 'Default')]
    assert 'Created default Tenant' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_db_upgrade_does_not_duplicate_the_default_tenant_on_a_rerun(
    sqlite_root_config, tmp_path, capsys
):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    capsys.readouterr()  # discard first run's "Created default Tenant" output

    result = await db_upgrade(sqlite_root_config, argparse.Namespace())
    assert result == 0

    db_path = tmp_path / 'loom_cli_test.db'
    conn = sqlite3.connect(db_path)
    count = conn.execute('SELECT COUNT(*) FROM tenant').fetchone()[0]
    conn.close()
    assert count == 1
    assert 'Created default Tenant' not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_db_upgrade_does_not_seed_when_a_tenant_already_exists(
    sqlite_root_config, tmp_path
):
    """Seeding is an empty-table convenience, not an opinion about what a
    deployment's Tenants should look like -- an admin who already created
    their own Tenant (schema-migrated, but before this seed step ever ran)
    must not also get a 'default' one alongside it."""
    command.upgrade(_alembic_config(sqlite_root_config), 'head')
    db_path = tmp_path / 'loom_cli_test.db'
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO tenant (id, slug, name, created_at, updated_at) '
        "VALUES ('11111111-1111-1111-1111-111111111111', 'acme', 'Acme', "
        "'2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    await db_upgrade(sqlite_root_config, argparse.Namespace())

    conn = sqlite3.connect(db_path)
    slugs = {row[0] for row in conn.execute('SELECT slug FROM tenant')}
    conn.close()
    assert slugs == {'acme'}


@pytest.mark.asyncio
async def test_db_current_and_history_do_not_raise(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    assert await db_current(sqlite_root_config, argparse.Namespace()) == 0
    assert await db_history(sqlite_root_config, argparse.Namespace()) == 0


@pytest.mark.asyncio
async def test_db_revision_autogenerate_creates_new_file(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    versions_dir = pathlib.Path(
        str(importlib.resources.files('loom') / 'migrations' / 'versions')
    )
    before = set(versions_dir.glob('*.py'))
    try:
        result = await db_revision(
            sqlite_root_config,
            argparse.Namespace(message='add scratch table', autogenerate=False),
        )
        assert result == 0
        after = set(versions_dir.glob('*.py'))
        assert len(after - before) == 1
    finally:
        after = set(versions_dir.glob('*.py'))
        for new_file in after - before:
            new_file.unlink()
