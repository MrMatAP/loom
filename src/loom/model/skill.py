import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import (
    Base,
    PortableJSON,
    TimestampMixin,
    VersionedEntityMixin,
    current_version_index,
    enum_column,
    exactly_one_of,
)
from loom.model.enums import GraphNodeType, Layer, SkillKind


class Skill(Base, VersionedEntityMixin):
    """Composable capability: atomic (prompt/code) or composite (graph)."""

    __tablename__ = 'skill'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_skill_entity_version'),
        current_version_index('skill'),
        sa.CheckConstraint(
            "(kind = 'atomic' AND atomic_content IS NOT NULL AND json_type(atomic_content) != 'null') OR (kind = 'composite' AND (atomic_content IS NULL OR json_type(atomic_content) = 'null'))",
            name='ck_skill_atomic_content_matches_kind',
        ),
    )

    layer: Mapped[Layer] = mapped_column(enum_column(Layer, 'layer'))
    kind: Mapped[SkillKind] = mapped_column(enum_column(SkillKind, 'skill_kind'))
    is_entry_point: Mapped[bool] = mapped_column(sa.Boolean(), default=False)
    atomic_content: Mapped[dict | None] = mapped_column(PortableJSON, default=None)


class SkillGraphNode(Base, TimestampMixin):
    """One node (Agent/Skill/Tool reference) in a composite Skill's graph."""

    __tablename__ = 'skill_graph_node'
    __table_args__ = (
        sa.UniqueConstraint('skill_id', 'node_key', name='uq_skill_graph_node_key'),
        sa.CheckConstraint(
            exactly_one_of('agent_id', 'skill_ref_id', 'tool_id'),
            name='ck_skill_graph_node_one_ref',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), index=True
    )
    node_key: Mapped[str] = mapped_column(sa.String(255))
    node_type: Mapped[GraphNodeType] = mapped_column(
        enum_column(GraphNodeType, 'graph_node_type')
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('agent.id'), default=None
    )
    skill_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), default=None
    )
    tool_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), default=None
    )
    position: Mapped[dict | None] = mapped_column(PortableJSON, default=None)


class SkillGraphEdge(Base, TimestampMixin):
    """A directed edge between two nodes of the same Skill's graph."""

    __tablename__ = 'skill_graph_edge'
    __table_args__ = (
        sa.UniqueConstraint(
            'skill_id', 'from_node_id', 'to_node_id', name='uq_skill_graph_edge'
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), index=True
    )
    from_node_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill_graph_node.id')
    )
    to_node_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill_graph_node.id')
    )
