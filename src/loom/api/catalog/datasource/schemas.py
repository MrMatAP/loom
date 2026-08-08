import uuid

from pydantic import BaseModel

from loom.model.enums import DataSourceKind


class DataSourceCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    kind: DataSourceKind
    connection_binding_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
