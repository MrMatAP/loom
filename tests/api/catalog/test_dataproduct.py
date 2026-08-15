import pytest


@pytest.mark.asyncio
async def test_dataproduct_full_lifecycle(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/dataproducts',
        json={'name': 'Curated Orders', 'contract': {}},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    get_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/dataproducts/{entity_id}'
    )
    assert get_resp.status_code == 200

    version_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/dataproducts/{entity_id}/versions',
        json={'name': 'Curated Orders v2', 'contract': {}},
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2


@pytest.mark.asyncio
async def test_lineage_is_version_scoped(api_client, fake_principal, async_session):
    from loom.model.datasource import DataSource
    from loom.model.enums import DataSourceKind

    tenant_id = fake_principal.tenant_id
    datasource = DataSource(
        tenant_id=fake_principal.tenant_id,
        owner_id=fake_principal.principal_id,
        created_by_id=fake_principal.principal_id,
        name='Orders DB',
        kind=DataSourceKind.DATABASE,
    )
    async_session.add(datasource)
    await async_session.commit()

    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/dataproducts',
        json={'name': 'Curated Orders', 'contract': {}},
    )
    entity_id = create_resp.json()['entity_id']

    lineage_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/dataproducts/{entity_id}/versions/1/lineage',
        json={'source_datasource_id': str(datasource.id)},
    )
    assert lineage_resp.status_code == 201

    list_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/dataproducts/{entity_id}/versions/1/lineage'
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
