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
    """A user, agent, or service account that can own entities or hold a
    RoleBinding. No `display_name` -- a human-readable name lives in the
    IDP (the token's `name` claim), not duplicated here; see
    `src/loom/cli/auth.py`'s `auth_whoami`. `tenant_id` here (not a token
    claim) is the sole source of which Tenant a caller belongs to -- see
    docs/admin-guide.md's "Platform administrator" section.

    `uq_principal_tenant_external_id` below only enforces uniqueness of
    `external_id` *per Tenant*, not globally -- the same IDP `sub` may
    legitimately hold a Principal row in more than one Tenant. That's
    deliberate, not an oversight: `resolve_principal`
    (`src/loom/api/catalog/dependencies.py`) disambiguates such an
    identity at request time via the `X-Loom-Tenant-Id` header, set
    locally with `loom auth set-tenant`."""

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
    external_id: Mapped[str] = mapped_column(sa.String(255))
