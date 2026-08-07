import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import (
    Base,
    PortableJSON,
    VersionedEntityMixin,
    current_version_index,
    enum_column,
)
from loom.model.enums import Layer, MemoryScope


class Agent(Base, VersionedEntityMixin):
    """Autonomous reasoning unit: model config, prompt, memory scope, permission boundary."""

    __tablename__ = 'agent'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_agent_entity_version'),
        current_version_index('agent'),
    )

    layer: Mapped[Layer] = mapped_column(enum_column(Layer, 'layer'))
    llm_config: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    prompt: Mapped[str] = mapped_column(sa.Text())
    memory_scope: Mapped[MemoryScope] = mapped_column(
        enum_column(MemoryScope, 'memory_scope')
    )
    permission_boundary: Mapped[dict] = mapped_column(PortableJSON, default=dict)
