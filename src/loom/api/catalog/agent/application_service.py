import uuid

from loom.api.catalog.base_application_service import VersionedApplicationService
from loom.domain.agent import Agent as DomainAgent
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.agent import AgentRead

from .schemas import AgentCreateRequest


class AgentApplicationService(VersionedApplicationService[DomainAgent, AgentRead]):
    label = 'Agent'
    read_cls = AgentRead

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, repository_attr='agents')

    async def create(
        self, *, tenant_id: uuid.UUID, created_by_id: uuid.UUID, data: AgentCreateRequest
    ) -> AgentRead:
        await self._repo.assert_model_binding_is_current_in_tenant(
            tenant_id, data.model_binding_id
        )
        agent = DomainAgent(
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
        saved = await self._repo.add(agent)
        return self._read(saved)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: AgentCreateRequest,
    ) -> AgentRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        await self._repo.assert_model_binding_is_current_in_tenant(
            tenant_id, data.model_binding_id
        )
        new_version = current.new_version(
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
        await self._repo.save(current)
        saved = await self._repo.add(new_version)
        return self._read(saved)
