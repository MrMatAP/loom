import contextlib
import importlib.resources
import io
import pathlib
import re
import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


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
        'model_endpoint',
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
    assert len(created) == 18


def test_no_migration_redeclares_an_earlier_migrations_postgresql_enum_type():
    """Each migration is normally applied to a real database in its own
    `loom db upgrade` invocation (one per release), not chained together in
    a single `command.upgrade(..., 'head')` call like the test above. That
    matters: alembic only memoizes "this enum type was already CREATE
    TYPE'd" *within a single upgrade invocation's connection*, so a shared
    enum (e.g. `lifecycle_state`, reused by many versioned-entity tables)
    silently renders fine when every migration is applied together in one
    shot -- the earlier migration's CREATE TYPE satisfies the memo for the
    later one -- but 500s a real database with `DuplicateObject` once the
    two migrations run as separate invocations against a database that
    already has the type from the first.

    Render each migration's own delta as its own offline `command.upgrade`
    call to reproduce that reality, and assert no enum type name is
    CREATE TYPE'd by more than one migration in the whole history. A
    migration that legitimately reuses an earlier one's enum (as opposed to
    introducing a new one) must mark that column
    `postgresql.ENUM(..., create_type=False)`.
    """
    config = _alembic_config_for_url('postgresql://u:p@localhost/db')
    script = ScriptDirectory.from_config(config)

    revisions = list(script.walk_revisions(base='base', head='head'))
    revisions.reverse()  # walk_revisions yields head-first; we want oldest-first

    first_creator: dict[str, str] = {}
    for rev in revisions:
        down = rev.down_revision or 'base'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            command.upgrade(config, f'{down}:{rev.revision}', sql=True)
        for name in re.findall(r'CREATE TYPE (\w+)', buf.getvalue()):
            assert name not in first_creator, (
                f'{rev.revision} re-declares CREATE TYPE {name}, first created by '
                f'{first_creator[name]} -- would fail with DuplicateObject against '
                f'a database that already ran {first_creator[name]} in a separate '
                f'`loom db upgrade`. Mark that column postgresql.ENUM(..., '
                f'create_type=False) in {rev.revision}.'
            )
            first_creator[name] = rev.revision
