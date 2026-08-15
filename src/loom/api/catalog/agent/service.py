import uuid

from loom.api.catalog.base import BaseService
from loom.model.agent import Agent
from loom.model.model_endpoint import ModelEndpoint

from .schemas import AgentCreateRequest


class AgentService(BaseService[Agent]):
    """Use-cases for the Agent aggregate."""

    label = 'Agent'

    async def _validate_model_binding(
        self, tenant_id: uuid.UUID, model_binding_id: uuid.UUID | None
    ) -> None:
        """model_binding_id holds a ModelEndpoint.entity_id (floating, not a
        specific version row), so it resolves via the current-version
        check, not a by-row-id one."""
        if model_binding_id is None:
            return
        await self._repository.assert_current_version_in_tenant(
            tenant_id, ModelEndpoint, model_binding_id
        )

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: AgentCreateRequest,
    ) -> Agent:
        await self._validate_model_binding(tenant_id, data.model_binding_id)
        agent = Agent(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            layer=data.layer,
            model_binding_id=data.model_binding_id,
            llm_config=data.llm_config,
            prompt=data.prompt,
            memory_scope=data.memory_scope,
            permission_boundary=data.permission_boundary,
        )
        return await self._repository.add(agent)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: AgentCreateRequest,
    ) -> Agent:
        current = await self.get_current(tenant_id, entity_id)
        await self._validate_model_binding(tenant_id, data.model_binding_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Agent(
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
            model_binding_id=data.model_binding_id,
            llm_config=data.llm_config,
            prompt=data.prompt,
            memory_scope=data.memory_scope,
            permission_boundary=data.permission_boundary,
        )
        return await self._repository.add(new_version)
