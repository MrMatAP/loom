import dataclasses
import decimal
import uuid

from loom.domain.base import AggregateRoot
from loom.domain.enums import RealizingEntityType
from loom.domain.errors import ValidationError


@dataclasses.dataclass(kw_only=True)
class Capability(AggregateRoot):
    """First-class, versioned business-meaning concept; all other entities
    realize it via `CapabilityRealization`."""

    target_metrics: list[dict] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class CapabilityRealization:
    """Join: an Agent/Skill/Tool version realizes a Capability, weighted.
    Reachable only through the owning Capability Aggregate -- no
    repository of its own (CONTEXT.md's "Aggregate (root)" entry)."""

    realizing_entity_type: RealizingEntityType
    contribution_weight: decimal.Decimal
    realizing_agent_id: uuid.UUID | None = None
    realizing_skill_id: uuid.UUID | None = None
    realizing_tool_id: uuid.UUID | None = None
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        refs = [
            self.realizing_agent_id,
            self.realizing_skill_id,
            self.realizing_tool_id,
        ]
        if sum(ref is not None for ref in refs) != 1:
            raise ValidationError(
                'A CapabilityRealization must reference exactly one of '
                'realizing_agent_id/realizing_skill_id/realizing_tool_id'
            )
        expected = {
            RealizingEntityType.AGENT: self.realizing_agent_id,
            RealizingEntityType.SKILL: self.realizing_skill_id,
            RealizingEntityType.TOOL: self.realizing_tool_id,
        }[self.realizing_entity_type]
        if expected is None:
            raise ValidationError(
                f'realizing_entity_type={self.realizing_entity_type} but its '
                f'matching reference id is unset'
            )
