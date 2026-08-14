from loom.model.datasource import DataSource
from loom.model.enums import DataSourceKind
from loom.model.schemas.datasource import DataSourceCreate, DataSourceRead
from loom.model.tenant import Principal, Tenant


def test_datasource_round_trip(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', external_id='ada')
    session.add(principal)
    session.commit()

    payload = DataSourceCreate(
        slug='orders-db',
        name='Orders DB',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        kind=DataSourceKind.DATABASE,
    )
    datasource = DataSource(**payload.model_dump())
    session.add(datasource)
    session.commit()

    read = DataSourceRead.model_validate(datasource)
    assert read.kind == DataSourceKind.DATABASE
