"""Make AuditEvent.actor_principal_id nullable

Revision ID: d20f105d640d
Revises: 0ba8c681a027
Create Date: 2026-08-14 00:00:00.000000

A platform administrator (see docs/admin-guide.md's "Platform
administrator" section) has no Principal row -- they bootstrap Tenants/
Principals through the Catalog API on the strength of an IDP role alone.
AuditEvent still needs to record their actions, so `actor_principal_id`
can no longer be a required FK; `AuditActor.external_id` (the token's
`sub`) is what keeps such an event attributable to a real identity instead
(see `src/loom/api/catalog/audit.py`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd20f105d640d'
down_revision: str | None = '0ba8c681a027'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('audit_event') as batch_op:
        batch_op.alter_column(
            'actor_principal_id', existing_type=sa.Uuid(), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table('audit_event') as batch_op:
        batch_op.alter_column(
            'actor_principal_id', existing_type=sa.Uuid(), nullable=False
        )
