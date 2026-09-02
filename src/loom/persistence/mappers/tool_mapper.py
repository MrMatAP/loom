from loom.domain.tool import Tool as DomainTool
from loom.domain.tool import ToolDataBinding as DomainBinding
from loom.persistence.mappers.base import base_domain_kwargs, base_row_kwargs
from loom.persistence.tool import Tool as ToolRow
from loom.persistence.tool import ToolDataBinding as BindingRow


def to_domain(row: ToolRow) -> DomainTool:
    return DomainTool(
        **base_domain_kwargs(row),
        invocation_spec=row.invocation_spec,
        auth_binding_id=row.auth_binding_id,
    )


def to_row(entity: DomainTool) -> ToolRow:
    kwargs = base_row_kwargs(entity)
    kwargs.update(
        invocation_spec=entity.invocation_spec, auth_binding_id=entity.auth_binding_id
    )
    return ToolRow(**kwargs)


def binding_to_domain(row: BindingRow) -> DomainBinding:
    return DomainBinding(
        id=row.id,
        datasource_id=row.datasource_id,
        dataproduct_id=row.dataproduct_id,
        access_mode=row.access_mode,
    )


def binding_to_row(binding: DomainBinding, *, tool_row_id) -> BindingRow:
    return BindingRow(
        id=binding.id,
        tool_id=tool_row_id,
        datasource_id=binding.datasource_id,
        dataproduct_id=binding.dataproduct_id,
        access_mode=binding.access_mode,
    )
