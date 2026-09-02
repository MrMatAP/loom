import uuid

from pydantic import BaseModel

from loom.domain.enums import Layer, MemoryScope


class AgentCreateRequest(BaseModel):
    name: str
    description: str | None = None
    layer: Layer
    model_binding_id: uuid.UUID | None = None
    llm_config: dict
    prompt: str
    memory_scope: MemoryScope
    permission_boundary: dict = {}
