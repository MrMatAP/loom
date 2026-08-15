import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import assert_same_tenant, flush_or_raise
from loom.model.capability import Capability, CapabilityRealization
from loom.model.enums import LifecycleState


class CapabilityRepository:
    """Async persistence access for Capability, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assert_same_tenant(
        self, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
    ) -> None:
        """Raise unless the referenced entity resolves inside this tenant."""
        await assert_same_tenant(self._session, tenant_id, model, entity_id)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> Capability | None:
        return await self._session.scalar(
            sa.select(Capability).where(
                Capability.tenant_id == tenant_id,
                Capability.entity_id == entity_id,
                Capability.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Capability | None:
        return await self._session.scalar(
            sa.select(Capability).where(
                Capability.tenant_id == tenant_id,
                Capability.entity_id == entity_id,
                Capability.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Capability]:
        result = await self._session.scalars(
            sa.select(Capability)
            .where(Capability.tenant_id == tenant_id, Capability.entity_id == entity_id)
            .order_by(Capability.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Capability], int]:
        stmt = sa.select(Capability).where(
            Capability.tenant_id == tenant_id, Capability.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Capability.lifecycle_state == lifecycle_state)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Capability.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, capability: Capability) -> Capability:
        self._session.add(capability)
        await flush_or_raise(self._session)
        return capability

    async def save(self, capability: Capability) -> Capability:
        await flush_or_raise(self._session)
        return capability

    async def add_realization(
        self, realization: CapabilityRealization
    ) -> CapabilityRealization:
        self._session.add(realization)
        await flush_or_raise(self._session)
        return realization

    async def list_realizations(
        self, capability_version_id: uuid.UUID
    ) -> list[CapabilityRealization]:
        result = await self._session.scalars(
            sa.select(CapabilityRealization).where(
                CapabilityRealization.capability_id == capability_version_id
            )
        )
        return list(result)
