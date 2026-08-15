import uuid

from loom.api.catalog.base import BaseService
from loom.model.dataproduct import DataProduct
from loom.model.datasource import DataSource
from loom.model.tool import Tool, ToolDataBinding

from .schemas import ToolCreateRequest, ToolDataBindingCreateRequest


class ToolService(BaseService[Tool]):
    """Use-cases for the Tool aggregate."""

    label = 'Tool'

    async def create(
        self, *, tenant_id: uuid.UUID, created_by_id: uuid.UUID, data: ToolCreateRequest
    ) -> Tool:
        tool = Tool(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            invocation_spec=data.invocation_spec,
            auth_binding_id=data.auth_binding_id,
        )
        return await self._repository.add(tool)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: ToolCreateRequest,
    ) -> Tool:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Tool(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            # Ownership carries over from the prior version -- there's no
            # more owner_id input to override it with (always inferred,
            # never caller-supplied).
            owner_id=current.owner_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            invocation_spec=data.invocation_spec,
            auth_binding_id=data.auth_binding_id,
        )
        return await self._repository.add(new_version)

    async def add_data_binding(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: ToolDataBindingCreateRequest,
    ) -> ToolDataBinding:
        tool = await self.get_version(tenant_id, entity_id, version)
        for model, referenced_id in (
            (DataSource, data.datasource_id),
            (DataProduct, data.dataproduct_id),
        ):
            if referenced_id is not None:
                await self._repository.assert_same_tenant(
                    tenant_id, model, referenced_id
                )
        binding = ToolDataBinding(
            tool_id=tool.id,
            datasource_id=data.datasource_id,
            dataproduct_id=data.dataproduct_id,
            access_mode=data.access_mode,
        )
        return await self._repository.add_data_binding(binding)

    async def list_data_bindings(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[ToolDataBinding]:
        tool = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_data_bindings(tool.id)
