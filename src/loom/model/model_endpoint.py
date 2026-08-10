import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import (
    Base,
    VersionedEntityMixin,
    current_version_index,
    enum_column,
)
from loom.model.enums import ModelProtocol


class ModelEndpoint(Base, VersionedEntityMixin):
    """Governed connection to an LLM inference endpoint (hosted or a
    generic OpenAI-compatible server); Agents bind to one via
    `Agent.model_binding_id`."""

    __tablename__ = 'model_endpoint'
    __table_args__ = (
        sa.UniqueConstraint(
            'entity_id', 'version', name='uq_model_endpoint_entity_version'
        ),
        current_version_index('model_endpoint'),
        sa.CheckConstraint(
            "(protocol != 'openai_compatible') OR (base_url IS NOT NULL)",
            name='ck_model_endpoint_openai_compatible_requires_base_url',
        ),
    )

    protocol: Mapped[ModelProtocol] = mapped_column(
        enum_column(ModelProtocol, 'model_protocol')
    )
    base_url: Mapped[str | None] = mapped_column(sa.String(2048), default=None)
    model: Mapped[str] = mapped_column(sa.String(255))
    auth_binding_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)
