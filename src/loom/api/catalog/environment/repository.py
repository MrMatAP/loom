import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.persistence.environment import Environment


class EnvironmentRepository:
    """Async persistence access for Environment (platform tier)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, tenant_id: uuid.UUID, environment_id: uuid.UUID
    ) -> Environment | None:
        return await self._session.scalar(
            sa.select(Environment).where(
                Environment.id == environment_id, Environment.tenant_id == tenant_id
            )
        )

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Environment], int]:
        stmt = sa.select(Environment).where(Environment.tenant_id == tenant_id)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Environment.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, environment: Environment) -> Environment:
        self._session.add(environment)
        await flush_or_raise(self._session)
        return environment

    async def save(self, environment: Environment) -> Environment:
        await flush_or_raise(self._session)
        return environment
