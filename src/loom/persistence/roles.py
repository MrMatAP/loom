"""Provisioning of the dedicated, DML-only Postgres roles the API and MCP
servers connect as. The database itself stays owned by the `loom-admin`
connection (full DDL, since it runs the schema migrations); these roles can
read and write rows but never alter the schema."""

import psycopg
from psycopg import sql

from loom.config import DatabaseConfig


def role_statements(
    role: str, password: str, database: str, owner: str
) -> list[sql.Composable]:
    """The statements that create `role` (or rotate its password, if it
    already exists) and grant it DML only. Every statement is idempotent, so
    re-running is safe.

    `ALTER DEFAULT PRIVILEGES FOR ROLE {owner}` is what keeps this working
    across migrations: tables created later by `owner` (the role `loom-admin`
    connects as and migrates with) are granted to `role` automatically, so
    the grants don't need to be re-run after `db upgrade`."""
    ident = sql.Identifier(role)
    dml = sql.SQL('SELECT, INSERT, UPDATE, DELETE')
    return [
        sql.SQL(
            'DO $$ BEGIN '
            'IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN '
            'CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; '
            'END IF; END $$'
        ).format(name=sql.Literal(role), role=ident),
        sql.SQL('ALTER ROLE {role} LOGIN PASSWORD {password}').format(
            role=ident, password=sql.Literal(password)
        ),
        sql.SQL('GRANT CONNECT ON DATABASE {db} TO {role}').format(
            db=sql.Identifier(database), role=ident
        ),
        sql.SQL('GRANT USAGE ON SCHEMA public TO {role}').format(role=ident),
        sql.SQL('REVOKE CREATE ON SCHEMA public FROM {role}').format(role=ident),
        sql.SQL('GRANT {dml} ON ALL TABLES IN SCHEMA public TO {role}').format(
            dml=dml, role=ident
        ),
        sql.SQL(
            'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}'
        ).format(role=ident),
        sql.SQL(
            'ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public '
            'GRANT {dml} ON TABLES TO {role}'
        ).format(owner=sql.Identifier(owner), dml=dml, role=ident),
        sql.SQL(
            'ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public '
            'GRANT USAGE, SELECT ON SEQUENCES TO {role}'
        ).format(owner=sql.Identifier(owner), role=ident),
        # Alembic's bookkeeping table is the admin's alone.
        sql.SQL(
            'DO $$ BEGIN '
            "IF to_regclass('public.alembic_version') IS NOT NULL THEN "
            'REVOKE ALL ON public.alembic_version FROM {role}; '
            'END IF; END $$'
        ).format(role=ident),
    ]


def provision_role(admin_db: DatabaseConfig, role: str, password: str) -> None:
    """Create/refresh `role` with DML-only grants, connecting as `admin_db`
    (the database owner)."""
    with psycopg.connect(
        host=admin_db.host,
        port=admin_db.port,
        dbname=admin_db.database,
        user=admin_db.username,
        password=admin_db.password.get_secret_value() if admin_db.password else None,
        autocommit=False,
    ) as conn:
        for statement in role_statements(
            role, password, admin_db.database, admin_db.username
        ):
            conn.execute(statement)
