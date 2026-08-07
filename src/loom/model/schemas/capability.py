import decimal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from loom.model.enums import RealizingEntityType
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class CapabilityCreate(VersionedEntityCreate):
    target_metrics: list[dict] = Field(default_factory=list)


class CapabilityRead(VersionedEntityRead):
    target_metrics: list[dict]


class CapabilityRealizationCreate(BaseModel):
    capability_id: uuid.UUID
    realizing_entity_type: RealizingEntityType
    realizing_agent_id: uuid.UUID | None = None
    realizing_skill_id: uuid.UUID | None = None
    realizing_tool_id: uuid.UUID | None = None
    contribution_weight: decimal.Decimal


class CapabilityRealizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    capability_id: uuid.UUID
    realizing_entity_type: RealizingEntityType
    realizing_agent_id: uuid.UUID | None
    realizing_skill_id: uuid.UUID | None
    realizing_tool_id: uuid.UUID | None
    contribution_weight: decimal.Decimal
