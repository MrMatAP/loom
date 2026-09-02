"""Exercises a real Postgres instance instead of the in-memory sqlite the
rest of the suite uses -- migrated via the packaged Alembic revisions, the
same path `loom db upgrade` runs in production.
"""

import uuid

import pytest
import sqlalchemy as sa

from loom.domain.enums import PrincipalKind
from loom.persistence.tenant import Principal, Tenant

from .live import IT_PREFIX
from .live_db import requires_live_db

pytestmark = [pytest.mark.live_db, requires_live_db]


def test_migrations_produce_the_expected_tables(live_db_engine):
    inspector = sa.inspect(live_db_engine)
    tables = set(inspector.get_table_names())
    assert {'tenant', 'principal'} <= tables


def test_tenant_and_principal_round_trip(live_db_session):
    """Writes through the real ORM models against real Postgres types
    (native UUID, the `principal_kind` enum, the tenant/external_id unique
    constraint) -- exactly what `get_principal_in_tenant` reads at request
    time. Rolled back by `live_db_session`, so nothing here persists."""
    tenant = Tenant(slug=f'{IT_PREFIX}-tenant', name='Loom Integration Test Tenant')
    live_db_session.add(tenant)
    live_db_session.flush()

    principal = Principal(
        tenant_id=tenant.id,
        kind=PrincipalKind.USER,
        external_id=str(uuid.uuid4()),
    )
    live_db_session.add(principal)
    live_db_session.flush()

    found = live_db_session.scalar(
        sa.select(Principal).where(
            Principal.tenant_id == tenant.id,
            Principal.external_id == principal.external_id,
        )
    )
    assert found is not None
    assert found.id == principal.id
