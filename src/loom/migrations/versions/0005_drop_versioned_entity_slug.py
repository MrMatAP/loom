"""Drop slug from every VersionedEntityMixin entity

Revision ID: 3f6b9d2c8a41
Revises: 7a3e9c1f4b6d
Create Date: 2026-08-15 00:00:00.000000

Nothing looks a Capability/Agent/Skill/Tool/DataSource/DataProduct/
ModelEndpoint up by `slug` -- every read/update/transition route addresses
it by `entity_id` (UUID); `slug` was write-only (set at create, an
optional list filter, never a lookup key). `Tenant.slug` is unrelated and
untouched -- it's the one slug with a real human-identifier role (`loom
tenant create <slug> <name>`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '3f6b9d2c8a41'
down_revision: str | None = '7a3e9c1f4b6d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    'agent',
    'capability',
    'dataproduct',
    'datasource',
    'model_endpoint',
    'skill',
    'tool',
)


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f'ix_{table}_slug')
            batch_op.drop_column('slug')


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            # `server_default=''` so this actually runs against a table
            # that already has rows -- there's no value to backfill a
            # dropped column with, but re-adding it NOT NULL with no
            # default would just fail the downgrade outright on any
            # non-empty deployment.
            batch_op.add_column(
                sa.Column(
                    'slug', sa.String(length=255), nullable=False, server_default=''
                )
            )
            batch_op.create_index(f'ix_{table}_slug', ['slug'], unique=False)
