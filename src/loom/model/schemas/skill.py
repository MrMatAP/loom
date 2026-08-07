import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import GraphNodeType, Layer, SkillKind
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class SkillCreate(VersionedEntityCreate):
    layer: Layer
    kind: SkillKind
    is_entry_point: bool = False
    atomic_content: dict | None = None


class SkillRead(VersionedEntityRead):
    layer: Layer
    kind: SkillKind
    is_entry_point: bool
    atomic_content: dict | None


class SkillGraphNodeCreate(BaseModel):
    skill_id: uuid.UUID
    node_key: str
    node_type: GraphNodeType
    agent_id: uuid.UUID | None = None
    skill_ref_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    position: dict | None = None


class SkillGraphNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    node_key: str
    node_type: GraphNodeType
    agent_id: uuid.UUID | None
    skill_ref_id: uuid.UUID | None
    tool_id: uuid.UUID | None
    position: dict | None


class SkillGraphEdgeCreate(BaseModel):
    skill_id: uuid.UUID
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID


class SkillGraphEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
