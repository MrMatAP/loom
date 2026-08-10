import uuid

from pydantic import BaseModel

from loom.model.enums import Layer, MemoryScope


class AgentCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    layer: Layer
    model_binding_id: uuid.UUID | None = None
    llm_config: dict
    prompt: str
    memory_scope: MemoryScope
    permission_boundary: dict = {}
    owner_id: uuid.UUID | None = None
