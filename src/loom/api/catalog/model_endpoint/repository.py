import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import assert_same_tenant, flush_or_raise
from loom.model.enums import LifecycleState
from loom.model.model_endpoint import ModelEndpoint


class ModelEndpointRepository:
    """Async persistence access for ModelEndpoint, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assert_same_tenant(
        self, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
    ) -> None:
        """Raise unless the referenced entity resolves inside this tenant."""
        await assert_same_tenant(self._session, tenant_id, model, entity_id)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> ModelEndpoint | None:
        return await self._session.scalar(
            sa.select(ModelEndpoint).where(
                ModelEndpoint.tenant_id == tenant_id,
                ModelEndpoint.entity_id == entity_id,
                ModelEndpoint.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> ModelEndpoint | None:
        return await self._session.scalar(
            sa.select(ModelEndpoint).where(
                ModelEndpoint.tenant_id == tenant_id,
                ModelEndpoint.entity_id == entity_id,
                ModelEndpoint.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[ModelEndpoint]:
        result = await self._session.scalars(
            sa.select(ModelEndpoint)
            .where(
                ModelEndpoint.tenant_id == tenant_id,
                ModelEndpoint.entity_id == entity_id,
            )
            .order_by(ModelEndpoint.version)
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
    ) -> tuple[list[ModelEndpoint], int]:
        stmt = sa.select(ModelEndpoint).where(
            ModelEndpoint.tenant_id == tenant_id, ModelEndpoint.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(ModelEndpoint.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(ModelEndpoint.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(ModelEndpoint.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, model_endpoint: ModelEndpoint) -> ModelEndpoint:
        self._session.add(model_endpoint)
        await flush_or_raise(self._session)
        return model_endpoint

    async def save(self, model_endpoint: ModelEndpoint) -> ModelEndpoint:
        await flush_or_raise(self._session)
        return model_endpoint
