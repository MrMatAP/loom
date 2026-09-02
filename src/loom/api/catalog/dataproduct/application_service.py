import uuid

from loom.api.catalog.base_application_service import VersionedApplicationService
from loom.domain.dataproduct import DataProduct as DomainDataProduct
from loom.domain.dataproduct import DataProductLineage as DomainLineage
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.dataproduct import DataProductLineageRead, DataProductRead

from .schemas import DataProductCreateRequest, DataProductLineageCreateRequest


def _lineage_read(
    lineage: DomainLineage, *, dataproduct_row_id: uuid.UUID
) -> DataProductLineageRead:
    return DataProductLineageRead(
        id=lineage.id,
        dataproduct_id=dataproduct_row_id,
        source_datasource_id=lineage.source_datasource_id,
        source_dataproduct_id=lineage.source_dataproduct_id,
    )


class DataProductApplicationService(
    VersionedApplicationService[DomainDataProduct, DataProductRead]
):
    label = 'DataProduct'
    read_cls = DataProductRead

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, repository_attr='dataproducts')

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProductRead:
        dataproduct = DomainDataProduct(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            contract=data.contract,
        )
        saved = await self._repo.add(dataproduct)
        return self._read(saved)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProductRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        new_version = current.new_version(
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            contract=data.contract,
        )
        await self._repo.save(current)
        saved = await self._repo.add(new_version)
        return self._read(saved)

    async def add_lineage(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: DataProductLineageCreateRequest,
    ) -> DataProductLineageRead:
        dataproduct = await self._get_version_or_raise(tenant_id, entity_id, version)
        await self._repo.assert_lineage_source_in_tenant(
            tenant_id,
            source_datasource_id=data.source_datasource_id,
            source_dataproduct_id=data.source_dataproduct_id,
        )
        lineage = DomainLineage(
            source_datasource_id=data.source_datasource_id,
            source_dataproduct_id=data.source_dataproduct_id,
        )
        saved = await self._repo.add_lineage(dataproduct.id, lineage)
        return _lineage_read(saved, dataproduct_row_id=dataproduct.id)

    async def list_lineage(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[DataProductLineageRead]:
        dataproduct = await self._get_version_or_raise(tenant_id, entity_id, version)
        lineage = await self._repo.list_lineage(dataproduct.id)
        return [_lineage_read(entry, dataproduct_row_id=dataproduct.id) for entry in lineage]
