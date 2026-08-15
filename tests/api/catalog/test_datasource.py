import pytest


@pytest.mark.asyncio
async def test_datasource_full_lifecycle(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/datasources',
        json={'name': 'Orders DB', 'kind': 'database'},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    get_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/datasources/{entity_id}'
    )
    assert get_resp.status_code == 200
    assert get_resp.json()['kind'] == 'database'

    version_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/datasources/{entity_id}/versions',
        json={'name': 'Orders DB v2', 'kind': 'database'},
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2

    transition_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/datasources/{entity_id}/versions/2/transitions',
        json={'to_state': 'in_review'},
    )
    assert transition_resp.status_code == 200


@pytest.mark.asyncio
async def test_datasource_not_found_returns_404(api_client, fake_principal):
    resp = await api_client.get(
        f'/api/v1/tenants/{fake_principal.tenant_id}/datasources/'
        '00000000-0000-0000-0000-000000000000'
    )
    assert resp.status_code == 404
