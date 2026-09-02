import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.domain.dataproduct import DataProductLineage as DomainLineage
from loom.persistence.dataproduct import DataProduct as DataProductRow
from loom.persistence.dataproduct import DataProductLineage as LineageRow
from loom.persistence.datasource import DataSource as DataSourceRow
from loom.persistence.db import assert_same_tenant, flush_or_raise
from loom.persistence.mappers import dataproduct_mapper
from loom.persistence.repositories.base_repository import VersionedRepository


class DataProductRepository(VersionedRepository):
    """The persistence gateway for the DataProduct Aggregate -- and only
    the DataProduct Aggregate: `DataProductLineage` has no repository of
    its own (CONTEXT.md's "Aggregate (root)" entry)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            row_cls=DataProductRow,
            to_domain=dataproduct_mapper.to_domain,
            to_row=dataproduct_mapper.to_row,
        )

    async def assert_lineage_source_in_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        source_datasource_id: uuid.UUID | None,
        source_dataproduct_id: uuid.UUID | None,
    ) -> None:
        for row_cls, referenced_id in (
            (DataSourceRow, source_datasource_id),
            (DataProductRow, source_dataproduct_id),
        ):
            if referenced_id is not None:
                await assert_same_tenant(self._session, tenant_id, row_cls, referenced_id)

    async def add_lineage(
        self, dataproduct_row_id: uuid.UUID, lineage: DomainLineage
    ) -> DomainLineage:
        row = dataproduct_mapper.lineage_to_row(
            lineage, dataproduct_row_id=dataproduct_row_id
        )
        self._session.add(row)
        await flush_or_raise(self._session)
        return lineage

    async def list_lineage(self, dataproduct_row_id: uuid.UUID) -> list[DomainLineage]:
        rows = await self._session.scalars(
            sa.select(LineageRow).where(LineageRow.dataproduct_id == dataproduct_row_id)
        )
        return [dataproduct_mapper.lineage_to_domain(row) for row in rows]
