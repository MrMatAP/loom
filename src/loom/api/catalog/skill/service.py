import uuid

from loom.api.catalog.base import BaseService
from loom.api.catalog.exceptions import EntityNotFoundError
from loom.persistence.agent import Agent
from loom.persistence.skill import Skill, SkillGraphEdge, SkillGraphNode
from loom.persistence.tool import Tool

from .schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)


class SkillService(BaseService[Skill]):
    """Use-cases for the Skill aggregate."""

    label = 'Skill'

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: SkillCreateRequest,
    ) -> Skill:
        skill = Skill(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            layer=data.layer,
            kind=data.kind,
            is_entry_point=data.is_entry_point,
            atomic_content=data.atomic_content,
        )
        return await self._repository.add(skill)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: SkillCreateRequest,
    ) -> Skill:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Skill(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            # Ownership carries over from the prior version -- there's no
            # more owner_id input to override it with (always inferred,
            # never caller-supplied).
            owner_id=current.owner_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            layer=data.layer,
            kind=data.kind,
            is_entry_point=data.is_entry_point,
            atomic_content=data.atomic_content,
        )
        return await self._repository.add(new_version)

    async def add_node(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphNodeCreateRequest,
    ) -> SkillGraphNode:
        skill = await self.get_version(tenant_id, entity_id, version)
        for model, referenced_id in (
            (Agent, data.agent_id),
            (Skill, data.skill_ref_id),
            (Tool, data.tool_id),
        ):
            if referenced_id is not None:
                await self._repository.assert_same_tenant(
                    tenant_id, model, referenced_id
                )
        node = SkillGraphNode(
            skill_id=skill.id,
            node_key=data.node_key,
            node_type=data.node_type,
            agent_id=data.agent_id,
            skill_ref_id=data.skill_ref_id,
            tool_id=data.tool_id,
            position=data.position,
        )
        return await self._repository.add_node(node)

    async def list_nodes(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphNode]:
        skill = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_nodes(skill.id)

    async def add_edge(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphEdgeCreateRequest,
    ) -> SkillGraphEdge:
        skill = await self.get_version(tenant_id, entity_id, version)
        for node_id in (data.from_node_id, data.to_node_id):
            if await self._repository.get_node(skill.id, node_id) is None:
                detail = (
                    f'Node {node_id} does not belong to Skill {entity_id} '
                    f'version {version}'
                )
                raise EntityNotFoundError(detail)
        edge = SkillGraphEdge(
            skill_id=skill.id,
            from_node_id=data.from_node_id,
            to_node_id=data.to_node_id,
        )
        return await self._repository.add_edge(edge)

    async def list_edges(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphEdge]:
        skill = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_edges(skill.id)
