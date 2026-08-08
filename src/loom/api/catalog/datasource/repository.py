import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.datasource import DataSource
from loom.model.enums import LifecycleState


class DataSourceRepository:
    """Async persistence access for DataSource, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DataSource | None:
        return await self._session.scalar(
            sa.select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.entity_id == entity_id,
                DataSource.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataSource | None:
        return await self._session.scalar(
            sa.select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.entity_id == entity_id,
                DataSource.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataSource]:
        result = await self._session.scalars(
            sa.select(DataSource)
            .where(DataSource.tenant_id == tenant_id, DataSource.entity_id == entity_id)
            .order_by(DataSource.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataSource], int]:
        stmt = sa.select(DataSource).where(
            DataSource.tenant_id == tenant_id, DataSource.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(DataSource.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(DataSource.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(DataSource.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, datasource: DataSource) -> DataSource:
        self._session.add(datasource)
        await flush_or_raise(self._session)
        return datasource

    async def save(self, datasource: DataSource) -> DataSource:
        await flush_or_raise(self._session)
        return datasource
