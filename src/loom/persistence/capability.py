import decimal
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.domain.enums import RealizingEntityType
from loom.persistence.base import (
    Base,
    PortableJSON,
    TimestampMixin,
    VersionedEntityMixin,
    current_version_index,
    enum_column,
    exactly_one_of,
)


class Capability(Base, VersionedEntityMixin):
    """First-class concept defining target metrics; all entities realize it."""

    __tablename__ = 'capability'
    __table_args__ = (
        sa.UniqueConstraint(
            'entity_id', 'version', name='uq_capability_entity_version'
        ),
        current_version_index('capability'),
    )

    target_metrics: Mapped[list] = mapped_column(PortableJSON, default=list)


class CapabilityRealization(Base, TimestampMixin):
    """Join: an Agent/Skill/Tool version realizes a Capability, weighted."""

    __tablename__ = 'capability_realization'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of(
                'realizing_agent_id', 'realizing_skill_id', 'realizing_tool_id'
            ),
            name='ck_capability_realization_one_realizer',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    capability_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('capability.id'), index=True
    )
    realizing_entity_type: Mapped[RealizingEntityType] = mapped_column(
        enum_column(RealizingEntityType, 'realizing_entity_type')
    )
    realizing_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('agent.id'), default=None
    )
    realizing_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), default=None
    )
    realizing_tool_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), default=None
    )
    contribution_weight: Mapped[decimal.Decimal] = mapped_column(sa.Numeric(5, 4))
