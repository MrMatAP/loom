import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.agent import Agent
from loom.model.capability import Capability, CapabilityRealization
from loom.model.enums import LifecycleState
from loom.model.skill import Skill
from loom.model.tenant import Principal
from loom.model.tool import Tool

from .repository import CapabilityRepository
from .schemas import CapabilityCreateRequest, CapabilityRealizationCreateRequest


class CapabilityService:
    """Use-cases for the Capability aggregate."""

    def __init__(self, repository: CapabilityRepository) -> None:
        self._repository = repository

    async def _resolve_owner_id(
        self, tenant_id: uuid.UUID, owner_id: uuid.UUID | None, fallback: uuid.UUID
    ) -> uuid.UUID:
        if owner_id is None:
            return fallback
        await self._repository.assert_same_tenant(tenant_id, Principal, owner_id)
        return owner_id

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: CapabilityCreateRequest,
    ) -> Capability:
        owner_id = await self._resolve_owner_id(tenant_id, data.owner_id, created_by_id)
        capability = Capability(
            tenant_id=tenant_id,
            owner_id=owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
        )
        return await self._repository.add(capability)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> Capability:
        capability = await self._repository.get_current(tenant_id, entity_id)
        if capability is None:
            raise EntityNotFoundError(f'Capability {entity_id} not found')
        return capability

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Capability:
        capability = await self._repository.get_version(tenant_id, entity_id, version)
        if capability is None:
            detail = f'Capability {entity_id} version {version} not found'
            raise EntityNotFoundError(detail)
        return capability

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Capability]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'Capability {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Capability], int]:
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
        data: CapabilityCreateRequest,
    ) -> Capability:
        current = await self.get_current(tenant_id, entity_id)
        owner_id = await self._resolve_owner_id(
            tenant_id, data.owner_id, current.owner_id
        )
        current.is_current = False
        await self._repository.save(current)
        new_version = Capability(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
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
    ) -> Capability:
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

    async def add_realization(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: CapabilityRealizationCreateRequest,
    ) -> CapabilityRealization:
        capability = await self.get_version(tenant_id, entity_id, version)
        for model, referenced_id in (
            (Agent, data.realizing_agent_id),
            (Skill, data.realizing_skill_id),
            (Tool, data.realizing_tool_id),
        ):
            if referenced_id is not None:
                await self._repository.assert_same_tenant(
                    tenant_id, model, referenced_id
                )
        realization = CapabilityRealization(
            capability_id=capability.id,
            realizing_entity_type=data.realizing_entity_type,
            realizing_agent_id=data.realizing_agent_id,
            realizing_skill_id=data.realizing_skill_id,
            realizing_tool_id=data.realizing_tool_id,
            contribution_weight=data.contribution_weight,
        )
        return await self._repository.add_realization(realization)

    async def list_realizations(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[CapabilityRealization]:
        capability = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_realizations(capability.id)
