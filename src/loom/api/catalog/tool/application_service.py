import uuid

from loom.api.catalog.base_application_service import VersionedApplicationService
from loom.domain.tool import Tool as DomainTool
from loom.domain.tool import ToolDataBinding as DomainBinding
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.tool import ToolDataBindingRead, ToolRead

from .schemas import ToolCreateRequest, ToolDataBindingCreateRequest


def _binding_read(binding: DomainBinding, *, tool_row_id: uuid.UUID) -> ToolDataBindingRead:
    return ToolDataBindingRead(
        id=binding.id,
        tool_id=tool_row_id,
        datasource_id=binding.datasource_id,
        dataproduct_id=binding.dataproduct_id,
        access_mode=binding.access_mode,
    )


class ToolApplicationService(VersionedApplicationService[DomainTool, ToolRead]):
    label = 'Tool'
    read_cls = ToolRead

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, repository_attr='tools')

    async def create(
        self, *, tenant_id: uuid.UUID, created_by_id: uuid.UUID, data: ToolCreateRequest
    ) -> ToolRead:
        tool = DomainTool(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            invocation_spec=data.invocation_spec,
            auth_binding_id=data.auth_binding_id,
        )
        saved = await self._repo.add(tool)
        return self._read(saved)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: ToolCreateRequest,
    ) -> ToolRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        new_version = current.new_version(
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            invocation_spec=data.invocation_spec,
            auth_binding_id=data.auth_binding_id,
        )
        await self._repo.save(current)
        saved = await self._repo.add(new_version)
        return self._read(saved)

    async def add_data_binding(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: ToolDataBindingCreateRequest,
    ) -> ToolDataBindingRead:
        tool = await self._get_version_or_raise(tenant_id, entity_id, version)
        await self._repo.assert_binding_target_in_tenant(
            tenant_id,
            datasource_id=data.datasource_id,
            dataproduct_id=data.dataproduct_id,
        )
        binding = DomainBinding(
            datasource_id=data.datasource_id,
            dataproduct_id=data.dataproduct_id,
            access_mode=data.access_mode,
        )
        saved = await self._repo.add_data_binding(tool.id, binding)
        return _binding_read(saved, tool_row_id=tool.id)

    async def list_data_bindings(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[ToolDataBindingRead]:
        tool = await self._get_version_or_raise(tenant_id, entity_id, version)
        bindings = await self._repo.list_data_bindings(tool.id)
        return [_binding_read(b, tool_row_id=tool.id) for b in bindings]
