import uuid

import sqlalchemy as sa

from loom.api.catalog.base import BaseRepository
from loom.api.catalog.db import flush_or_raise
from loom.persistence.dataproduct import DataProduct, DataProductLineage


class DataProductRepository(BaseRepository[DataProduct]):
    """Async persistence access for DataProduct, scoped by tenant."""

    @property
    def model(self) -> type[DataProduct]:
        return DataProduct

    async def add_lineage(self, lineage: DataProductLineage) -> DataProductLineage:
        self._session.add(lineage)
        await flush_or_raise(self._session)
        return lineage

    async def list_lineage(
        self, dataproduct_version_id: uuid.UUID
    ) -> list[DataProductLineage]:
        result = await self._session.scalars(
            sa.select(DataProductLineage).where(
                DataProductLineage.dataproduct_id == dataproduct_version_id
            )
        )
        return list(result)
