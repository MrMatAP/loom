import pytest
import sqlalchemy as sa

from loom.domain.enums import DataBindingAccessMode, DataSourceKind
from loom.persistence.dataproduct import DataProduct, DataProductLineage
from loom.persistence.datasource import DataSource
from loom.persistence.tenant import Principal, Tenant
from loom.persistence.tool import Tool, ToolDataBinding
from loom.schemas.dataproduct import DataProductCreate, DataProductRead
from loom.schemas.tool import ToolCreate, ToolDataBindingCreate


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
