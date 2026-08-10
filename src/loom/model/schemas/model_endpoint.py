import uuid

from loom.model.enums import ModelProtocol
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class ModelEndpointCreate(VersionedEntityCreate):
    protocol: ModelProtocol
    base_url: str | None = None
    model: str
    auth_binding_id: uuid.UUID | None = None


class ModelEndpointRead(VersionedEntityRead):
    protocol: ModelProtocol
    base_url: str | None
    model: str
    auth_binding_id: uuid.UUID | None
