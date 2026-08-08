import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.enums import LifecycleState
from loom.model.skill import Skill, SkillGraphEdge, SkillGraphNode

from .repository import SkillRepository
from .schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)


class SkillService:
    """Use-cases for the Skill aggregate."""

    def __init__(self, repository: SkillRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: SkillCreateRequest,
    ) -> Skill:
        skill = Skill(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            layer=data.layer,
            kind=data.kind,
            is_entry_point=data.is_entry_point,
            atomic_content=data.atomic_content,
        )
        return await self._repository.add(skill)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Skill:
        skill = await self._repository.get_current(tenant_id, entity_id)
        if skill is None:
            raise EntityNotFoundError(f'Skill {entity_id} not found')
        return skill

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Skill:
        skill = await self._repository.get_version(tenant_id, entity_id, version)
        if skill is None:
            raise EntityNotFoundError(f'Skill {entity_id} version {version} not found')
        return skill

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Skill]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'Skill {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Skill], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

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
            owner_id=data.owner_id or current.owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            layer=data.layer,
            kind=data.kind,
            is_entry_point=data.is_entry_point,
            atomic_content=data.atomic_content,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> Skill:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = (
                f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            )
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)

    async def add_node(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphNodeCreateRequest,
    ) -> SkillGraphNode:
        skill = await self.get_version(tenant_id, entity_id, version)
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
