import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.agent import Agent
from loom.model.enums import LifecycleState


class AgentRepository:
    """Async persistence access for Agent, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> Agent | None:
        return await self._session.scalar(
            sa.select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.entity_id == entity_id,
                Agent.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Agent | None:
        return await self._session.scalar(
            sa.select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.entity_id == entity_id,
                Agent.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Agent]:
        result = await self._session.scalars(
            sa.select(Agent)
            .where(Agent.tenant_id == tenant_id, Agent.entity_id == entity_id)
            .order_by(Agent.version)
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
    ) -> tuple[list[Agent], int]:
        stmt = sa.select(Agent).where(
            Agent.tenant_id == tenant_id, Agent.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Agent.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(Agent.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Agent.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, agent: Agent) -> Agent:
        self._session.add(agent)
        await flush_or_raise(self._session)
        return agent

    async def save(self, agent: Agent) -> Agent:
        await flush_or_raise(self._session)
        return agent
