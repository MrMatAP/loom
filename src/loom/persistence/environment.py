import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.domain.enums import EnvironmentKind
from loom.persistence.base import Base, TimestampMixin, enum_column


class Environment(Base, TimestampMixin):
    """Isolated execution context (Sandbox/Staging/Production) with data boundary."""

    __tablename__ = 'environment'
    __table_args__ = (
        sa.UniqueConstraint('tenant_id', 'name', name='uq_environment_tenant_name'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(255))
    kind: Mapped[EnvironmentKind] = mapped_column(
        enum_column(EnvironmentKind, 'environment_kind')
    )
    compute_boundary_ref: Mapped[str] = mapped_column(sa.String(255))
    network_boundary_ref: Mapped[str] = mapped_column(sa.String(255))
