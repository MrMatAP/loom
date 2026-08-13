import argparse
import importlib.resources
import pathlib
import sqlite3

import pytest
import sqlalchemy as sa

from loom.cli.db import (
    db_current,
    db_downgrade,
    db_history,
    db_revision,
    db_seed_principal,
    db_upgrade,
)
from loom.config import RootConfig
from loom.model.engine import get_session_factory
from loom.model.governance import AuditEvent
from loom.model.tenant import Principal, Tenant


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


def _seed_args(**overrides):
    defaults = {
        'tenant_slug': 'acme',
        'tenant_name': 'Acme Corp',
        'kind': 'user',
        'display_name': 'Mathieu Imfeld',
        'external_id': 'sub-123',
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.mark.asyncio
async def test_seed_principal_creates_tenant_and_principal(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())

    result = await db_seed_principal(sqlite_root_config, _seed_args())
    assert result == 0

    with get_session_factory(sqlite_root_config.database)() as session:
        tenant = session.scalar(sa.select(Tenant).where(Tenant.slug == 'acme'))
        assert tenant is not None
        assert tenant.name == 'Acme Corp'

        principal = session.scalar(
            sa.select(Principal).where(Principal.external_id == 'sub-123')
        )
        assert principal is not None
        assert principal.tenant_id == tenant.id
        assert principal.kind.value == 'user'

        audit_event = session.scalar(
            sa.select(AuditEvent).where(AuditEvent.entity_id == principal.id)
        )
        assert audit_event is not None
        assert audit_event.action == 'cli_bootstrap_seed_principal'
        assert audit_event.actor_principal_id == principal.id
        assert audit_event.decision.value == 'allow'


@pytest.mark.asyncio
async def test_seed_principal_reuses_existing_tenant_by_slug(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    await db_seed_principal(sqlite_root_config, _seed_args())

    # No --tenant-name this time -- must not require it for a Tenant that
    # already exists, and must not create a second Tenant with the same slug.
    result = await db_seed_principal(
        sqlite_root_config,
        _seed_args(tenant_name=None, external_id='sub-456', display_name='Second User'),
    )
    assert result == 0

    with get_session_factory(sqlite_root_config.database)() as session:
        tenants = session.scalars(sa.select(Tenant).where(Tenant.slug == 'acme')).all()
        assert len(tenants) == 1
        principals = session.scalars(
            sa.select(Principal).where(Principal.tenant_id == tenants[0].id)
        ).all()
        assert {p.external_id for p in principals} == {'sub-123', 'sub-456'}


@pytest.mark.asyncio
async def test_seed_principal_is_safe_to_rerun_for_the_same_identity(
    sqlite_root_config, capsys
):
    """Must report the existing Principal rather than crashing on the
    (tenant_id, external_id) unique constraint."""
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    await db_seed_principal(sqlite_root_config, _seed_args())

    result = await db_seed_principal(sqlite_root_config, _seed_args())
    assert result == 0
    assert 'already exists' in capsys.readouterr().out

    with get_session_factory(sqlite_root_config.database)() as session:
        principals = session.scalars(
            sa.select(Principal).where(Principal.external_id == 'sub-123')
        ).all()
        assert len(principals) == 1


@pytest.mark.asyncio
async def test_seed_principal_requires_tenant_name_for_a_new_tenant(
    sqlite_root_config, capsys
):
    await db_upgrade(sqlite_root_config, argparse.Namespace())

    result = await db_seed_principal(sqlite_root_config, _seed_args(tenant_name=None))
    assert result == 1
    assert '--tenant-name is required' in capsys.readouterr().out

    with get_session_factory(sqlite_root_config.database)() as session:
        assert session.scalar(sa.select(Tenant).where(Tenant.slug == 'acme')) is None
