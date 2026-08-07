import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, TimestampMixin
from loom.model.enums import PrincipalKind


class Tenant(Base, TimestampMixin):
    """Isolated org boundary that Environments and Principals scope to."""

    __tablename__ = 'tenant'

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(sa.String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(sa.String(255))


class Principal(Base, TimestampMixin):
    """A user, agent, or service account that can own entities or hold a RoleBinding."""

    __tablename__ = 'principal'
    __table_args__ = (
        sa.UniqueConstraint(
            'tenant_id',
            'external_id',
            name='uq_principal_tenant_external_id',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    kind: Mapped[PrincipalKind] = mapped_column(
        sa.Enum(
            PrincipalKind,
            name='principal_kind',
            values_callable=lambda obj: [e.value for e in obj],
        )
    )
    display_name: Mapped[str] = mapped_column(sa.String(255))
    external_id: Mapped[str] = mapped_column(sa.String(255))
