import dataclasses
import uuid

from loom.domain.base import AggregateRoot
from loom.domain.enums import ModelProtocol
from loom.domain.errors import ValidationError


@dataclasses.dataclass(kw_only=True)
class ModelEndpoint(AggregateRoot):
    """Governed connection to an LLM inference endpoint (hosted or a
    generic OpenAI-compatible server); Agents bind to one via
    `Agent.model_binding_id`."""

    protocol: ModelProtocol
    model: str
    base_url: str | None = None
    auth_binding_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.protocol == ModelProtocol.OPENAI_COMPATIBLE and self.base_url is None:
            raise ValidationError(
                'An OpenAI-compatible ModelEndpoint requires base_url'
            )
