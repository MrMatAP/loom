import uuid

from pydantic import BaseModel

from loom.model.enums import DataBindingAccessMode


class ToolCreateRequest(BaseModel):
    name: str
    description: str | None = None
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None = None


class ToolDataBindingCreateRequest(BaseModel):
    datasource_id: uuid.UUID | None = None
    dataproduct_id: uuid.UUID | None = None
    access_mode: DataBindingAccessMode
