import uuid

from pydantic import Field

from loom.model.enums import Layer, MemoryScope
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class AgentCreate(VersionedEntityCreate):
    layer: Layer
    model_binding_id: uuid.UUID | None = None
    llm_config: dict
    prompt: str
    memory_scope: MemoryScope
    permission_boundary: dict = Field(default_factory=dict)


class AgentRead(VersionedEntityRead):
    layer: Layer
    model_binding_id: uuid.UUID | None
    llm_config: dict
    prompt: str
    memory_scope: MemoryScope
    permission_boundary: dict
