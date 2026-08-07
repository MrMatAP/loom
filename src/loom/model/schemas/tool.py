import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import DataBindingAccessMode
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class ToolCreate(VersionedEntityCreate):
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None = None


class ToolRead(VersionedEntityRead):
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None


class ToolDataBindingCreate(BaseModel):
    tool_id: uuid.UUID
    datasource_id: uuid.UUID | None = None
    dataproduct_id: uuid.UUID | None = None
    access_mode: DataBindingAccessMode


class ToolDataBindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_id: uuid.UUID
    datasource_id: uuid.UUID | None
    dataproduct_id: uuid.UUID | None
    access_mode: DataBindingAccessMode
