import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.domain.enums import Layer, MemoryScope
from loom.persistence.base import (
    Base,
    PortableJSON,
    VersionedEntityMixin,
    current_version_index,
    enum_column,
)


class Agent(Base, VersionedEntityMixin):
    """Autonomous reasoning unit: model, prompt, memory scope, permission boundary."""

    __tablename__ = 'agent'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_agent_entity_version'),
        current_version_index('agent'),
    )

    layer: Mapped[Layer] = mapped_column(enum_column(Layer, 'layer'))
    # Which ModelEndpoint this Agent talks to. Floating, not pinned: holds a
    # ModelEndpoint.entity_id and always resolves to whichever version is
    # currently `is_current` -- resolvable through the existing
    # GET /model-endpoints/{entity_id}, unlike a specific version row's `id`
    # (there is no by-row-id lookup). entity_id alone isn't a candidate key
    # (multiple version rows share it), so this can't be a DB-level FK; the
    # tenant/current-version check happens at the service layer instead,
    # same trade-off CLAUDE.md's Tool.data_bindings[] open question flags.
    # Nullable: a Draft agent can exist before a model is bound.
    model_binding_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)
    # Per-agent invocation overrides (temperature, max_tokens, ...) for
    # whatever model_binding_id points at -- not the endpoint's identity or
    # transport, which live on ModelEndpoint itself.
    llm_config: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    prompt: Mapped[str] = mapped_column(sa.Text())
    memory_scope: Mapped[MemoryScope] = mapped_column(
        enum_column(MemoryScope, 'memory_scope')
    )
    permission_boundary: Mapped[dict] = mapped_column(PortableJSON, default=dict)
