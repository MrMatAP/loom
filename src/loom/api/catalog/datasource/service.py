import uuid

from loom.api.catalog.base import BaseService
from loom.model.datasource import DataSource

from .schemas import DataSourceCreateRequest


class DataSourceService(BaseService[DataSource]):
    """Use-cases for the DataSource aggregate."""

    label = 'DataSource'

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSource:
        datasource = DataSource(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        return await self._repository.add(datasource)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSource:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = DataSource(
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
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        return await self._repository.add(new_version)
