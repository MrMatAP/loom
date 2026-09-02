import datetime
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from loom.domain.enums import AuditDecision, PolicyEffect, PolicyScopeType
from loom.persistence.base import Base, PortableJSON, TimestampMixin, enum_column

# Column-local JSON type for permission_subset that properly handles None as SQL NULL
_permission_subset_type = sa.JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True), 'postgresql'
)


class Policy(Base, TimestampMixin):
    """Governance rule: allow/deny effect over scope; Policy Engine evaluated."""

    __tablename__ = 'policy'

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text(), default=None)
    effect: Mapped[PolicyEffect] = mapped_column(
        enum_column(PolicyEffect, 'policy_effect')
    )
    scope_type: Mapped[PolicyScopeType] = mapped_column(
        enum_column(PolicyScopeType, 'policy_scope_type')
    )
    rule: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id')
    )


class RoleBinding(Base, TimestampMixin):
    """Grants a Principal a role over a scope; delegation is permission-limited."""

    __tablename__ = 'role_binding'
    __table_args__ = (
        sa.CheckConstraint(
            'delegated_from_principal_id IS NULL OR permission_subset IS NOT NULL',
            name='ck_role_binding_delegation_requires_subset',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), index=True
    )
    role: Mapped[str] = mapped_column(sa.String(255))
    scope_type: Mapped[PolicyScopeType] = mapped_column(
        enum_column(PolicyScopeType, 'policy_scope_type')
    )
    scope_ref: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('environment.id'), default=None
    )
    delegated_from_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), default=None
    )
    permission_subset: Mapped[dict | None] = mapped_column(
        _permission_subset_type, default=None
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id')
    )


class AuditEvent(Base):
    """Immutable, append-only audit trail entry (separate from Trace storage)."""

    __tablename__ = 'audit_event'

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    # Nullable: a platform administrator (see docs/admin-guide.md) has no
    # Principal row. `AuditActor.external_id` (src/loom/api/catalog/audit.py)
    # keeps such an event attributable to a real identity regardless.
    actor_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), default=None
    )
    acting_as_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), default=None
    )
    action: Mapped[str] = mapped_column(sa.String(255))
    entity_type: Mapped[str] = mapped_column(sa.String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('environment.id'), default=None
    )
    decision: Mapped[AuditDecision] = mapped_column(
        enum_column(AuditDecision, 'audit_decision')
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('policy.id'), default=None
    )
    details: Mapped[dict] = mapped_column(PortableJSON, default=dict)
