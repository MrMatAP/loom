import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import assert_same_tenant, flush_or_raise
from loom.model.dataproduct import DataProduct, DataProductLineage
from loom.model.enums import LifecycleState


class DataProductRepository:
    """Async persistence access for DataProduct, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assert_same_tenant(
        self, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
    ) -> None:
        """Raise unless the referenced entity resolves inside this tenant."""
        await assert_same_tenant(self._session, tenant_id, model, entity_id)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DataProduct | None:
        return await self._session.scalar(
            sa.select(DataProduct).where(
                DataProduct.tenant_id == tenant_id,
                DataProduct.entity_id == entity_id,
                DataProduct.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataProduct | None:
        return await self._session.scalar(
            sa.select(DataProduct).where(
                DataProduct.tenant_id == tenant_id,
                DataProduct.entity_id == entity_id,
                DataProduct.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataProduct]:
        result = await self._session.scalars(
            sa.select(DataProduct)
            .where(
                DataProduct.tenant_id == tenant_id, DataProduct.entity_id == entity_id
            )
            .order_by(DataProduct.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataProduct], int]:
        stmt = sa.select(DataProduct).where(
            DataProduct.tenant_id == tenant_id, DataProduct.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(DataProduct.lifecycle_state == lifecycle_state)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(DataProduct.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, dataproduct: DataProduct) -> DataProduct:
        self._session.add(dataproduct)
        await flush_or_raise(self._session)
        return dataproduct

    async def save(self, dataproduct: DataProduct) -> DataProduct:
        await flush_or_raise(self._session)
        return dataproduct

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
