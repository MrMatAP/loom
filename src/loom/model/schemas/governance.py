import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import AuditDecision, PolicyEffect, PolicyScopeType


class PolicyCreate(BaseModel):
    tenant_id: uuid.UUID
    name: str
    description: str | None = None
    effect: PolicyEffect
    scope_type: PolicyScopeType
    rule: dict
    created_by_id: uuid.UUID


class PolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    effect: PolicyEffect
    scope_type: PolicyScopeType
    rule: dict
    created_by_id: uuid.UUID


class PolicyUpdate(BaseModel):
    description: str | None = None
    rule: dict | None = None


class RoleBindingCreate(BaseModel):
    tenant_id: uuid.UUID
    principal_id: uuid.UUID
    role: str
    scope_type: PolicyScopeType
    scope_ref: dict
    environment_id: uuid.UUID | None = None
    delegated_from_principal_id: uuid.UUID | None = None
    permission_subset: dict | None = None
    created_by_id: uuid.UUID


class RoleBindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    principal_id: uuid.UUID
    role: str
    scope_type: PolicyScopeType
    scope_ref: dict
    environment_id: uuid.UUID | None
    delegated_from_principal_id: uuid.UUID | None
    permission_subset: dict | None
    created_by_id: uuid.UUID


class RoleBindingUpdate(BaseModel):
    role: str | None = None
    permission_subset: dict | None = None


class AuditEventCreate(BaseModel):
    tenant_id: uuid.UUID
    actor_principal_id: uuid.UUID
    acting_as_principal_id: uuid.UUID | None = None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    decision: AuditDecision
    policy_id: uuid.UUID | None = None
    details: dict = {}


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    occurred_at: datetime.datetime
    actor_principal_id: uuid.UUID
    acting_as_principal_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    decision: AuditDecision
    policy_id: uuid.UUID | None
    details: dict
