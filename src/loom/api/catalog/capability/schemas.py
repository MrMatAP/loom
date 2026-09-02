import decimal
import uuid

from pydantic import BaseModel

from loom.domain.enums import RealizingEntityType


class CapabilityCreateRequest(BaseModel):
    name: str
    description: str | None = None
    target_metrics: list[dict] = []


class CapabilityRealizationCreateRequest(BaseModel):
    realizing_entity_type: RealizingEntityType
    realizing_agent_id: uuid.UUID | None = None
    realizing_skill_id: uuid.UUID | None = None
    realizing_tool_id: uuid.UUID | None = None
    contribution_weight: decimal.Decimal
