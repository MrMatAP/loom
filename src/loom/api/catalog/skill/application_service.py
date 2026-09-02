"""Orchestrates the Skill Aggregate's use-cases: load via `UnitOfWork.skills`,
call a domain method, save, commit -- then convert to a Wire schema for the
caller (router or MCP tool). See CONTEXT.md's "Application Service" entry.
Replaces `SkillService`/`SkillRepository`'s old `BaseService`-shaped role.
"""

import datetime
import uuid

from loom.domain.enums import LifecycleState
from loom.domain.errors import IllegalTransitionError, NotFoundError
from loom.domain.skill import Skill as DomainSkill
from loom.domain.skill import SkillGraphEdge as DomainEdge
from loom.domain.skill import SkillGraphNode as DomainNode
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.skill import SkillGraphEdgeRead, SkillGraphNodeRead, SkillRead

from .schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)


def _skill_read(skill: DomainSkill) -> SkillRead:
    return SkillRead.model_validate(skill, from_attributes=True)


def _node_read(node: DomainNode, *, skill_row_id: uuid.UUID) -> SkillGraphNodeRead:
    return SkillGraphNodeRead(
        id=node.id,
        skill_id=skill_row_id,
        node_key=node.node_key,
        node_type=node.node_type,
        agent_id=node.agent_id,
        skill_ref_id=node.skill_ref_id,
        tool_id=node.tool_id,
        position=node.position,
    )


def _edge_read(edge: DomainEdge, *, skill_row_id: uuid.UUID) -> SkillGraphEdgeRead:
    return SkillGraphEdgeRead(
        id=edge.id,
        skill_id=skill_row_id,
        from_node_id=edge.from_node_id,
        to_node_id=edge.to_node_id,
    )


class SkillApplicationService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def create(
        self, *, tenant_id: uuid.UUID, created_by_id: uuid.UUID, data: SkillCreateRequest
    ) -> SkillRead:
        skill = DomainSkill(
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
        saved = await self._uow.skills.add(skill)
        return _skill_read(saved)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: SkillCreateRequest,
    ) -> SkillRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        new_version = current.new_version(
            created_by_id=created_by_id,
            name=data.name,
            layer=data.layer,
            kind=data.kind,
            description=data.description,
            is_entry_point=data.is_entry_point,
            atomic_content=data.atomic_content,
        )
        await self._uow.skills.save(current)  # persist the is_current=False flip
        saved = await self._uow.skills.add(new_version)
        return _skill_read(saved)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> SkillRead:
        return _skill_read(await self._get_current_or_raise(tenant_id, entity_id))

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> SkillRead:
        return _skill_read(
            await self._get_version_or_raise(tenant_id, entity_id, version)
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[SkillRead]:
        versions = await self._uow.skills.list_versions(tenant_id, entity_id)
        if not versions:
            raise NotFoundError(f'Skill {entity_id} not found')
        return [_skill_read(v) for v in versions]

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SkillRead], int]:
        skills, total = await self._uow.skills.list_current(
            tenant_id, lifecycle_state=lifecycle_state, limit=limit, offset=offset
        )
        return [_skill_read(s) for s in skills], total

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> SkillRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        if current.version != version:
            raise IllegalTransitionError(
                f'Version {version} is not the current version of {entity_id}'
            )
        current.transition(
            to_state, actor_id=actor_id, now=datetime.datetime.now(datetime.UTC)
        )
        saved = await self._uow.skills.save(current)
        return _skill_read(saved)

    async def add_node(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphNodeCreateRequest,
    ) -> SkillGraphNodeRead:
        skill = await self._get_version_or_raise(tenant_id, entity_id, version)
        resolved_layer = await self._uow.skills.resolve_node_layer(
            tenant_id,
            node_type=data.node_type,
            agent_id=data.agent_id,
            skill_ref_id=data.skill_ref_id,
            tool_id=data.tool_id,
        )
        node = skill.add_node(
            DomainNode(
                node_key=data.node_key,
                node_type=data.node_type,
                agent_id=data.agent_id,
                skill_ref_id=data.skill_ref_id,
                tool_id=data.tool_id,
                resolved_layer=resolved_layer,
                position=data.position,
            )
        )
        saved = await self._uow.skills.add_node(skill.id, node)
        return _node_read(saved, skill_row_id=skill.id)

    async def list_nodes(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphNodeRead]:
        skill = await self._get_version_or_raise(tenant_id, entity_id, version)
        nodes = await self._uow.skills.list_nodes(skill.id)
        return [_node_read(n, skill_row_id=skill.id) for n in nodes]

    async def add_edge(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphEdgeCreateRequest,
    ) -> SkillGraphEdgeRead:
        skill = await self._uow.skills.get_version_with_graph(tenant_id, entity_id, version)
        if skill is None:
            raise NotFoundError(f'Skill {entity_id} version {version} not found')
        edge = skill.add_edge(data.from_node_id, data.to_node_id)
        saved = await self._uow.skills.add_edge(skill.id, edge)
        return _edge_read(saved, skill_row_id=skill.id)

    async def list_edges(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphEdgeRead]:
        skill = await self._get_version_or_raise(tenant_id, entity_id, version)
        edges = await self._uow.skills.list_edges(skill.id)
        return [_edge_read(e, skill_row_id=skill.id) for e in edges]

    # -- internal ---------------------------------------------------

    async def _get_current_or_raise(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DomainSkill:
        skill = await self._uow.skills.get_current(tenant_id, entity_id)
        if skill is None:
            raise NotFoundError(f'Skill {entity_id} not found')
        return skill

    async def _get_version_or_raise(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DomainSkill:
        skill = await self._uow.skills.get_version(tenant_id, entity_id, version)
        if skill is None:
            raise NotFoundError(f'Skill {entity_id} version {version} not found')
        return skill
