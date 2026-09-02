import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.domain.tool import ToolDataBinding as DomainBinding
from loom.persistence.dataproduct import DataProduct as DataProductRow
from loom.persistence.datasource import DataSource as DataSourceRow
from loom.persistence.db import assert_same_tenant, flush_or_raise
from loom.persistence.mappers import tool_mapper
from loom.persistence.repositories.base_repository import VersionedRepository
from loom.persistence.tool import Tool as ToolRow
from loom.persistence.tool import ToolDataBinding as BindingRow


class ToolRepository(VersionedRepository):
    """The persistence gateway for the Tool Aggregate -- and only the Tool
    Aggregate: `ToolDataBinding` has no repository of its own (CONTEXT.md's
    "Aggregate (root)" entry)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            row_cls=ToolRow,
            to_domain=tool_mapper.to_domain,
            to_row=tool_mapper.to_row,
        )

    async def assert_binding_target_in_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        datasource_id: uuid.UUID | None,
        dataproduct_id: uuid.UUID | None,
    ) -> None:
        for row_cls, referenced_id in (
            (DataSourceRow, datasource_id),
            (DataProductRow, dataproduct_id),
        ):
            if referenced_id is not None:
                await assert_same_tenant(self._session, tenant_id, row_cls, referenced_id)

    async def add_data_binding(
        self, tool_row_id: uuid.UUID, binding: DomainBinding
    ) -> DomainBinding:
        row = tool_mapper.binding_to_row(binding, tool_row_id=tool_row_id)
        self._session.add(row)
        await flush_or_raise(self._session)
        return binding

    async def list_data_bindings(self, tool_row_id: uuid.UUID) -> list[DomainBinding]:
        rows = await self._session.scalars(
            sa.select(BindingRow).where(BindingRow.tool_id == tool_row_id)
        )
        return [tool_mapper.binding_to_domain(row) for row in rows]
