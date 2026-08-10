import uuid

from pydantic import BaseModel

from loom.model.enums import ModelProtocol


class ModelEndpointCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    protocol: ModelProtocol
    base_url: str | None = None
    model: str
    auth_binding_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
