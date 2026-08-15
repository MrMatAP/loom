import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import assert_same_tenant, flush_or_raise
from loom.model.enums import LifecycleState
from loom.model.tool import Tool, ToolDataBinding


class ToolRepository:
    """Async persistence access for Tool, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assert_same_tenant(
        self, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
    ) -> None:
        """Raise unless the referenced entity resolves inside this tenant."""
        await assert_same_tenant(self._session, tenant_id, model, entity_id)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> Tool | None:
        return await self._session.scalar(
            sa.select(Tool).where(
                Tool.tenant_id == tenant_id,
                Tool.entity_id == entity_id,
                Tool.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Tool | None:
        return await self._session.scalar(
            sa.select(Tool).where(
                Tool.tenant_id == tenant_id,
                Tool.entity_id == entity_id,
                Tool.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Tool]:
        result = await self._session.scalars(
            sa.select(Tool)
            .where(Tool.tenant_id == tenant_id, Tool.entity_id == entity_id)
            .order_by(Tool.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Tool], int]:
        stmt = sa.select(Tool).where(
            Tool.tenant_id == tenant_id, Tool.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Tool.lifecycle_state == lifecycle_state)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Tool.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, tool: Tool) -> Tool:
        self._session.add(tool)
        await flush_or_raise(self._session)
        return tool

    async def save(self, tool: Tool) -> Tool:
        await flush_or_raise(self._session)
        return tool

    async def add_data_binding(self, binding: ToolDataBinding) -> ToolDataBinding:
        self._session.add(binding)
        await flush_or_raise(self._session)
        return binding

    async def list_data_bindings(
        self, tool_version_id: uuid.UUID
    ) -> list[ToolDataBinding]:
        result = await self._session.scalars(
            sa.select(ToolDataBinding).where(ToolDataBinding.tool_id == tool_version_id)
        )
        return list(result)
