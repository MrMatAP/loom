import decimal
import uuid

from pydantic import BaseModel, ConfigDict


class MetricCreate(BaseModel):
    tenant_id: uuid.UUID
    capability_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    skill_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    metric_name: str
    value: decimal.Decimal
    unit: str | None = None
    is_realization_score: bool = False
    weakest_contributor_agent_id: uuid.UUID | None = None
    weakest_contributor_skill_id: uuid.UUID | None = None
    weakest_contributor_tool_id: uuid.UUID | None = None
    otel_resource_ref: str | None = None


class MetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    capability_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    skill_id: uuid.UUID | None
    tool_id: uuid.UUID | None
    metric_name: str
    value: decimal.Decimal
    unit: str | None
    is_realization_score: bool
    weakest_contributor_agent_id: uuid.UUID | None
    weakest_contributor_skill_id: uuid.UUID | None
    weakest_contributor_tool_id: uuid.UUID | None
    otel_resource_ref: str | None
