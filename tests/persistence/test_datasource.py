from loom.domain.enums import DataSourceKind
from loom.persistence.datasource import DataSource
from loom.persistence.tenant import Principal, Tenant
from loom.schemas.datasource import DataSourceCreate, DataSourceRead


def test_datasource_round_trip(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', external_id='ada')
    session.add(principal)
    session.commit()

    payload = DataSourceCreate(
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
