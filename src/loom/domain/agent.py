import dataclasses
import uuid

from loom.domain.base import AggregateRoot
from loom.domain.enums import Layer, MemoryScope


@dataclasses.dataclass(kw_only=True)
class Agent(AggregateRoot):
    """Autonomous reasoning unit: model, prompt, memory scope, permission boundary."""

    layer: Layer
    prompt: str
    memory_scope: MemoryScope
    # Which ModelEndpoint this Agent talks to -- floating, not pinned (see
    # CONTEXT.md's ModelEndpoint entry / CLAUDE.md's open question): holds
    # a ModelEndpoint.entity_id, resolved against its *current* version by
    # the Repository/ApplicationService, not checkable here since it needs
    # a query. Nullable: a Draft agent can exist before a model is bound.
    model_binding_id: uuid.UUID | None = None
    llm_config: dict = dataclasses.field(default_factory=dict)
    permission_boundary: dict = dataclasses.field(default_factory=dict)
