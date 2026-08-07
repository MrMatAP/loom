import uuid

from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class ToolCreate(VersionedEntityCreate):
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None = None


class ToolRead(VersionedEntityRead):
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None
