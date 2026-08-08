import pytest


@pytest.mark.asyncio
async def test_tenant_crud(api_client):
    create_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'acme', 'name': 'Acme Corp'}
    )
    assert create_resp.status_code == 201
    tenant_id = create_resp.json()['id']

    get_resp = await api_client.get(f'/api/v1/tenants/{tenant_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['slug'] == 'acme'

    list_resp = await api_client.get('/api/v1/tenants')
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] >= 1

    update_resp = await api_client.patch(
        f'/api/v1/tenants/{tenant_id}', json={'name': 'Acme Corp Inc'}
    )
    assert update_resp.status_code == 200
    assert update_resp.json()['name'] == 'Acme Corp Inc'


@pytest.mark.asyncio
async def test_principal_crud(api_client):
    tenant_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'globex', 'name': 'Globex'}
    )
    tenant_id = tenant_resp.json()['id']

    create_resp = await api_client.post(
        '/api/v1/principals',
        json={
            'tenant_id': tenant_id,
            'kind': 'user',
            'display_name': 'Ada',
            'external_id': 'ada@globex.example',
        },
    )
    assert create_resp.status_code == 201
    principal_id = create_resp.json()['id']

    get_resp = await api_client.get(f'/api/v1/principals/{principal_id}')
    assert get_resp.status_code == 200

    list_resp = await api_client.get(
        '/api/v1/principals', params={'tenant_id': tenant_id}
    )
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 1

    update_resp = await api_client.patch(
        f'/api/v1/principals/{principal_id}', json={'display_name': 'Ada Lovelace'}
    )
    assert update_resp.status_code == 200
    assert update_resp.json()['display_name'] == 'Ada Lovelace'


@pytest.mark.asyncio
async def test_environment_crud(api_client):
    tenant_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'initech', 'name': 'Initech'}
    )
    tenant_id = tenant_resp.json()['id']

    create_resp = await api_client.post(
        '/api/v1/environments',
        json={
            'tenant_id': tenant_id,
            'name': 'prod',
            'kind': 'production',
            'compute_boundary_ref': 'vpc-prod-compute',
            'network_boundary_ref': 'vpc-prod-net',
        },
    )
    assert create_resp.status_code == 201
    environment_id = create_resp.json()['id']

    list_resp = await api_client.get(
        '/api/v1/environments', params={'tenant_id': tenant_id}
    )
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 1

    update_resp = await api_client.patch(
        f'/api/v1/environments/{environment_id}',
        json={'compute_boundary_ref': 'vpc-prod-compute-v2'},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()['compute_boundary_ref'] == 'vpc-prod-compute-v2'
