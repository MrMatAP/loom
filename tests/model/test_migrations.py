import importlib.resources
import pathlib
import sqlite3

from alembic import command
from alembic.config import Config


def _alembic_config(db_path: pathlib.Path) -> Config:
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{db_path}')
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
