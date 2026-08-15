import uuid

from loom.api.catalog.base import BaseService
from loom.model.dataproduct import DataProduct, DataProductLineage
from loom.model.datasource import DataSource

from .schemas import DataProductCreateRequest, DataProductLineageCreateRequest


class DataProductService(BaseService[DataProduct]):
    """Use-cases for the DataProduct aggregate."""

    label = 'DataProduct'

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProduct:
        dataproduct = DataProduct(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            contract=data.contract,
        )
        return await self._repository.add(dataproduct)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProduct:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = DataProduct(
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
            contract=data.contract,
        )
        return await self._repository.add(new_version)

    async def add_lineage(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: DataProductLineageCreateRequest,
    ) -> DataProductLineage:
        dataproduct = await self.get_version(tenant_id, entity_id, version)
        for model, referenced_id in (
            (DataSource, data.source_datasource_id),
            (DataProduct, data.source_dataproduct_id),
        ):
            if referenced_id is not None:
                await self._repository.assert_same_tenant(
                    tenant_id, model, referenced_id
                )
        lineage = DataProductLineage(
            dataproduct_id=dataproduct.id,
            source_datasource_id=data.source_datasource_id,
            source_dataproduct_id=data.source_dataproduct_id,
        )
        return await self._repository.add_lineage(lineage)

    async def list_lineage(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[DataProductLineage]:
        dataproduct = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_lineage(dataproduct.id)
