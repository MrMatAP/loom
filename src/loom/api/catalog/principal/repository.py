import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.tenant import Principal


class PrincipalRepository:
    """Async persistence access for Principal (platform tier)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, tenant_id: uuid.UUID, principal_id: uuid.UUID
    ) -> Principal | None:
        return await self._session.scalar(
            sa.select(Principal).where(
                Principal.id == principal_id, Principal.tenant_id == tenant_id
            )
        )

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Principal], int]:
        stmt = sa.select(Principal).where(Principal.tenant_id == tenant_id)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Principal.external_id).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, principal: Principal) -> Principal:
        self._session.add(principal)
        await flush_or_raise(self._session)
        return principal
