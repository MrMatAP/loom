import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import EnvironmentKind


class EnvironmentCreate(BaseModel):
    tenant_id: uuid.UUID
    name: str
    kind: EnvironmentKind
    compute_boundary_ref: str
    network_boundary_ref: str


class EnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    kind: EnvironmentKind
    compute_boundary_ref: str
    network_boundary_ref: str


class EnvironmentUpdate(BaseModel):
    compute_boundary_ref: str | None = None
    network_boundary_ref: str | None = None
