import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.agent import Agent
from loom.model.enums import LifecycleState

from .repository import AgentRepository
from .schemas import AgentCreateRequest


class AgentService:
    """Use-cases for the Agent aggregate."""

    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: AgentCreateRequest,
    ) -> Agent:
        agent = Agent(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            layer=data.layer,
            llm_config=data.llm_config,
            prompt=data.prompt,
            memory_scope=data.memory_scope,
            permission_boundary=data.permission_boundary,
        )
        return await self._repository.add(agent)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Agent:
        agent = await self._repository.get_current(tenant_id, entity_id)
        if agent is None:
            raise EntityNotFoundError(f'Agent {entity_id} not found')
        return agent

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Agent:
        agent = await self._repository.get_version(tenant_id, entity_id, version)
        if agent is None:
            raise EntityNotFoundError(f'Agent {entity_id} version {version} not found')
        return agent

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Agent]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'Agent {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Agent], int]:
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
        data: AgentCreateRequest,
    ) -> Agent:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Agent(
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
            llm_config=data.llm_config,
            prompt=data.prompt,
            memory_scope=data.memory_scope,
            permission_boundary=data.permission_boundary,
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
    ) -> Agent:
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
