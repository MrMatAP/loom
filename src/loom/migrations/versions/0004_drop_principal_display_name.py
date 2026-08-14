"""Drop Principal.display_name

Revision ID: 7a3e9c1f4b6d
Revises: d20f105d640d
Create Date: 2026-08-14 00:00:00.000000

A human-readable name for a Principal now lives in the IDP (the token's
`name` claim), not duplicated here -- see `src/loom/cli/auth.py`'s
`auth_whoami` and `src/loom/model/tenant.py`'s `Principal` docstring.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7a3e9c1f4b6d'
down_revision: str | None = 'd20f105d640d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('principal') as batch_op:
        batch_op.drop_column('display_name')


def downgrade() -> None:
    with op.batch_alter_table('principal') as batch_op:
        # `server_default=''` so this actually runs against a table that
        # already has rows -- there's no value to backfill a dropped
        # column with, but re-adding it NOT NULL with no default would
        # just fail the downgrade outright on any non-empty deployment.
        batch_op.add_column(
            sa.Column(
                'display_name', sa.String(length=255), nullable=False, server_default=''
            )
        )
