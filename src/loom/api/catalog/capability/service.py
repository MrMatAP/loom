import uuid

from loom.api.catalog.base import BaseService
from loom.model.agent import Agent
from loom.model.capability import Capability, CapabilityRealization
from loom.model.skill import Skill
from loom.model.tool import Tool

from .schemas import CapabilityCreateRequest, CapabilityRealizationCreateRequest


class CapabilityService(BaseService[Capability]):
    """Use-cases for the Capability aggregate."""

    label = 'Capability'

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: CapabilityCreateRequest,
    ) -> Capability:
        capability = Capability(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
        )
        return await self._repository.add(capability)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: CapabilityCreateRequest,
    ) -> Capability:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Capability(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            # Ownership carries over from the prior version -- creating a
            # new version isn't a change-of-owner action; there's no more
            # `owner_id` input to override it with (see CLAUDE.md/the CLI
            # -- owner is always inferred, never caller-supplied).
            owner_id=current.owner_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
        )
        return await self._repository.add(new_version)

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
