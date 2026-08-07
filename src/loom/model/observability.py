import datetime
import decimal
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, TimestampMixin, exactly_one_of


class Metric(Base, TimestampMixin):
    """
    An observable signal, pipeline-derived. Scopes to exactly one of Capability/
    Agent/Skill/Tool. This is the rollup row only (e.g. `realization_score`) —
    raw spans and time-series points live in the separate OTel-backed Trace/
    Metrics store, not here.
    """

    __tablename__ = 'metric'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of('capability_id', 'agent_id', 'skill_id', 'tool_id'),
            name='ck_metric_one_scope',
        ),
        sa.CheckConstraint(
            '(weakest_contributor_agent_id IS NULL AND '
            'weakest_contributor_skill_id IS NULL '
            'AND weakest_contributor_tool_id IS NULL) OR '
            + exactly_one_of(
                'weakest_contributor_agent_id',
                'weakest_contributor_skill_id',
                'weakest_contributor_tool_id',
            ),
            name='ck_metric_weakest_contributor_at_most_one',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    capability_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('capability.id'), default=None
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('agent.id'), default=None
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), default=None
    )
    tool_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), default=None
    )
    metric_name: Mapped[str] = mapped_column(sa.String(255))
    value: Mapped[decimal.Decimal] = mapped_column(sa.Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    is_realization_score: Mapped[bool] = mapped_column(sa.Boolean(), default=False)
    weakest_contributor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('agent.id'), default=None
    )
    weakest_contributor_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), default=None
    )
    weakest_contributor_tool_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), default=None
    )
    otel_resource_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
