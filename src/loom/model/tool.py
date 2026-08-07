import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import (
    Base,
    PortableJSON,
    VersionedEntityMixin,
    current_version_index,
)


class Tool(Base, VersionedEntityMixin):
    """Static, design-time-bound interface for deterministic external automation."""

    __tablename__ = 'tool'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_tool_entity_version'),
        current_version_index('tool'),
    )

    invocation_spec: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    auth_binding_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)
