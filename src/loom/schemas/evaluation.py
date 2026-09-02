import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from loom.domain.enums import EvalRunStatus, LifecycleState, VersionedEntityKind


class EvalSuiteCreate(BaseModel):
    tenant_id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    target_entity_type: VersionedEntityKind
    criteria: dict
    created_by_id: uuid.UUID


class EvalSuiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    target_entity_type: VersionedEntityKind
    criteria: dict
    created_by_id: uuid.UUID


class EvalRunCreate(BaseModel):
    eval_suite_id: uuid.UUID
    target_entity_type: VersionedEntityKind
    target_agent_id: uuid.UUID | None = None
    target_skill_id: uuid.UUID | None = None
    target_tool_id: uuid.UUID | None = None
    target_capability_id: uuid.UUID | None = None
    target_datasource_id: uuid.UUID | None = None
    target_dataproduct_id: uuid.UUID | None = None
    status: EvalRunStatus = EvalRunStatus.PENDING
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    results: dict = {}
    triggered_by_id: uuid.UUID
    gates_transition_to: LifecycleState | None = None


class EvalRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    eval_suite_id: uuid.UUID
    target_entity_type: VersionedEntityKind
    target_agent_id: uuid.UUID | None
    target_skill_id: uuid.UUID | None
    target_tool_id: uuid.UUID | None
    target_capability_id: uuid.UUID | None
    target_datasource_id: uuid.UUID | None
    target_dataproduct_id: uuid.UUID | None
    status: EvalRunStatus
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    results: dict
    triggered_by_id: uuid.UUID
    gates_transition_to: LifecycleState | None
