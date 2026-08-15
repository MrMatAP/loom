import uuid

import sqlalchemy as sa

from loom.api.catalog.base import BaseRepository
from loom.api.catalog.db import flush_or_raise
from loom.model.tool import Tool, ToolDataBinding


class ToolRepository(BaseRepository[Tool]):
    """Async persistence access for Tool, scoped by tenant."""

    @property
    def model(self) -> type[Tool]:
        return Tool

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
