import pytest
import sqlalchemy as sa

from loom.model.dataproduct import DataProduct, DataProductLineage
from loom.model.datasource import DataSource
from loom.model.enums import DataBindingAccessMode, DataSourceKind
from loom.model.schemas.dataproduct import DataProductCreate, DataProductRead
from loom.model.schemas.tool import ToolCreate, ToolDataBindingCreate
from loom.model.tenant import Principal, Tenant
from loom.model.tool import Tool, ToolDataBinding


def _tenant_and_principal(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', external_id='ada')
    session.add(principal)
    session.commit()
    return tenant, principal


def test_dataproduct_and_lineage(session):
    tenant, principal = _tenant_and_principal(session)
    datasource = DataSource(
        name='Orders DB',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        kind=DataSourceKind.DATABASE,
    )
    session.add(datasource)
    session.commit()

    payload = DataProductCreate(
        name='Curated Orders',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        contract={'columns': ['order_id', 'total']},
    )
    dataproduct = DataProduct(**payload.model_dump())
    session.add(dataproduct)
    session.commit()

    session.add(
        DataProductLineage(
            dataproduct_id=dataproduct.id, source_datasource_id=datasource.id
        )
    )
    session.commit()

    read = DataProductRead.model_validate(dataproduct)
    assert read.contract['columns'] == ['order_id', 'total']

    with pytest.raises(sa.exc.IntegrityError):
        session.add(DataProductLineage(dataproduct_id=dataproduct.id))
        session.commit()


def test_tool_data_binding_is_version_pinned_and_exclusive(session):
    tenant, principal = _tenant_and_principal(session)
    datasource = DataSource(
        name='Orders DB',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        kind=DataSourceKind.DATABASE,
    )
    tool = Tool(
        **ToolCreate(
            name='Query Orders',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
            invocation_spec={},
        ).model_dump()
    )
    session.add_all([datasource, tool])
    session.commit()

    binding = ToolDataBinding(
        **ToolDataBindingCreate(
            tool_id=tool.id,
            datasource_id=datasource.id,
            access_mode=DataBindingAccessMode.READ,
        ).model_dump()
    )
    session.add(binding)
    session.commit()
    assert binding.datasource_id == datasource.id

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            ToolDataBinding(tool_id=tool.id, access_mode=DataBindingAccessMode.READ)
        )
        session.commit()
