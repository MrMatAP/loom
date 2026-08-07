import contextlib
import importlib.resources
import io
import pathlib
import re
import sqlite3

from alembic import command
from alembic.config import Config


def _alembic_config(db_path: pathlib.Path) -> Config:
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{db_path}')
    return config


def _alembic_config_for_url(url: str) -> Config:
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', url)
    return config


def test_upgrade_then_downgrade_round_trip(tmp_path):
    db_path = tmp_path / 'loom_migrations_test.db'
    config = _alembic_config(db_path)

    command.upgrade(config, 'head')
    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    expected = {
        'tenant',
        'principal',
        'environment',
        'agent',
        'tool',
        'datasource',
        'dataproduct',
        'dataproduct_lineage',
        'tool_data_binding',
        'skill',
        'skill_graph_node',
        'skill_graph_edge',
        'capability',
        'capability_realization',
        'metric',
        'policy',
        'role_binding',
        'audit_event',
        'eval_suite',
        'eval_run',
    }
    assert expected <= tables

    command.downgrade(config, 'base')
    conn = sqlite3.connect(db_path)
    remaining = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    } & expected
    conn.close()
    assert remaining == set()


def test_upgrade_downgrade_enum_types_match_on_postgresql():
    # sql=True renders offline SQL text and never opens a real connection,
    # so a postgresql:// URL is safe to use without a live Postgres server.
    config = _alembic_config_for_url('postgresql://u:p@localhost/db')

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(config, 'head', sql=True)
    created = set(re.findall(r'CREATE TYPE (\w+)', upgrade_sql.getvalue()))

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(config, 'head:base', sql=True)
    dropped = set(re.findall(r'DROP TYPE (\w+)', downgrade_sql.getvalue()))

    assert created == dropped
    assert len(created) == 17
