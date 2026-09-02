import uuid

from loom.api.catalog.base import BaseService
from loom.persistence.model_endpoint import ModelEndpoint

from .schemas import ModelEndpointCreateRequest


class ModelEndpointService(BaseService[ModelEndpoint]):
    """Use-cases for the ModelEndpoint aggregate."""

    label = 'ModelEndpoint'

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: ModelEndpointCreateRequest,
    ) -> ModelEndpoint:
        model_endpoint = ModelEndpoint(
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
        return await self._repository.add(model_endpoint)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: ModelEndpointCreateRequest,
    ) -> ModelEndpoint:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = ModelEndpoint(
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
            protocol=data.protocol,
            base_url=data.base_url,
            model=data.model,
            auth_binding_id=data.auth_binding_id,
        )
        return await self._repository.add(new_version)
