import datetime
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.domain.enums import EvalRunStatus, LifecycleState, VersionedEntityKind
from loom.persistence.base import (
    Base,
    PortableJSON,
    TimestampMixin,
    enum_column,
    exactly_one_of,
)


class EvalSuite(Base, TimestampMixin):
    """A named set of regression criteria targeting one versioned entity kind."""

    __tablename__ = 'eval_suite'
    __table_args__ = (
        sa.UniqueConstraint('tenant_id', 'slug', name='uq_eval_suite_tenant_slug'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    slug: Mapped[str] = mapped_column(sa.String(255))
    name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text(), default=None)
    target_entity_type: Mapped[VersionedEntityKind] = mapped_column(
        enum_column(VersionedEntityKind, 'versioned_entity_kind')
    )
    criteria: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id')
    )


class EvalRun(Base, TimestampMixin):
    """EvalSuite execution against an entity row; gates lifecycle transitions."""

    __tablename__ = 'eval_run'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of(
                'target_agent_id',
                'target_skill_id',
                'target_tool_id',
                'target_capability_id',
                'target_datasource_id',
                'target_dataproduct_id',
            ),
            name='ck_eval_run_one_target',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    eval_suite_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('eval_suite.id'), index=True
    )
    target_entity_type: Mapped[VersionedEntityKind] = mapped_column(
        enum_column(VersionedEntityKind, 'versioned_entity_kind')
    )
    target_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('agent.id'), default=None
    )
    target_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), default=None
    )
    target_tool_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), default=None
    )
    target_capability_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('capability.id'), default=None
    )
    target_datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('datasource.id'), default=None
    )
    target_dataproduct_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('dataproduct.id'), default=None
    )
    status: Mapped[EvalRunStatus] = mapped_column(
        enum_column(EvalRunStatus, 'eval_run_status'), default=EvalRunStatus.PENDING
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), default=None
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), default=None
    )
    results: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    triggered_by_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id')
    )
    gates_transition_to: Mapped[LifecycleState | None] = mapped_column(
        enum_column(LifecycleState, 'lifecycle_state'), default=None
    )
