import uuid

from pydantic import BaseModel

from loom.model.enums import GraphNodeType, Layer, SkillKind


class SkillCreateRequest(BaseModel):
    name: str
    description: str | None = None
    layer: Layer
    kind: SkillKind
    is_entry_point: bool = False
    atomic_content: dict | None = None


class SkillGraphNodeCreateRequest(BaseModel):
    node_key: str
    node_type: GraphNodeType
    agent_id: uuid.UUID | None = None
    skill_ref_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    position: dict | None = None


class SkillGraphEdgeCreateRequest(BaseModel):
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
