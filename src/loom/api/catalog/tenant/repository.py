import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.tenant import Tenant


class TenantRepository:
    """Async persistence access for Tenant, platform-wide (not tenant-scoped)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self._session.get(Tenant, tenant_id)

    async def list_all(self, *, limit: int, offset: int) -> tuple[list[Tenant], int]:
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(Tenant)
        )
        rows = await self._session.scalars(
            sa.select(Tenant).order_by(Tenant.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def list_by_ids(self, tenant_ids: set[uuid.UUID]) -> list[Tenant]:
        """Unordered-by-caller, small-set lookup -- backs `GET
        /tenants/mine` (`router.py`), never a paginated listing, so no
        limit/offset."""
        if not tenant_ids:
            return []
        rows = await self._session.scalars(
            sa.select(Tenant).where(Tenant.id.in_(tenant_ids)).order_by(Tenant.name)
        )
        return list(rows)

    async def add(self, tenant: Tenant) -> Tenant:
        self._session.add(tenant)
        await flush_or_raise(self._session)
        return tenant

    async def save(self, tenant: Tenant) -> Tenant:
        await flush_or_raise(self._session)
        return tenant
