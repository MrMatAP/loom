import uuid

from loom.api.catalog.base_application_service import VersionedApplicationService
from loom.domain.model_endpoint import ModelEndpoint as DomainModelEndpoint
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.model_endpoint import ModelEndpointRead

from .schemas import ModelEndpointCreateRequest


class ModelEndpointApplicationService(
    VersionedApplicationService[DomainModelEndpoint, ModelEndpointRead]
):
    label = 'ModelEndpoint'
    read_cls = ModelEndpointRead

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, repository_attr='model_endpoints')

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: ModelEndpointCreateRequest,
    ) -> ModelEndpointRead:
        model_endpoint = DomainModelEndpoint(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            protocol=data.protocol,
            base_url=data.base_url,
            model=data.model,
            auth_binding_id=data.auth_binding_id,
        )
        saved = await self._repo.add(model_endpoint)
        return self._read(saved)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: ModelEndpointCreateRequest,
    ) -> ModelEndpointRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        new_version = current.new_version(
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            protocol=data.protocol,
            base_url=data.base_url,
            model=data.model,
            auth_binding_id=data.auth_binding_id,
        )
        await self._repo.save(current)
        saved = await self._repo.add(new_version)
        return self._read(saved)
