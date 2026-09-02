import uuid

from loom.api.catalog.base_application_service import VersionedApplicationService
from loom.domain.datasource import DataSource as DomainDataSource
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.datasource import DataSourceRead

from .schemas import DataSourceCreateRequest


class DataSourceApplicationService(VersionedApplicationService[DomainDataSource, DataSourceRead]):
    label = 'DataSource'
    read_cls = DataSourceRead

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, repository_attr='datasources')

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSourceRead:
        datasource = DomainDataSource(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        saved = await self._repo.add(datasource)
        return self._read(saved)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSourceRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        new_version = current.new_version(
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        await self._repo.save(current)
        saved = await self._repo.add(new_version)
        return self._read(saved)
