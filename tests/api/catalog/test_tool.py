import pytest


@pytest.mark.asyncio
async def test_tool_full_lifecycle(api_client):
    create_resp = await api_client.post(
        '/api/v1/tools',
        json={
            'slug': 'send-email',
            'name': 'Send Email',
            'invocation_spec': {'method': 'POST', 'path': '/v1/email'},
        },
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    get_resp = await api_client.get(f'/api/v1/tools/{entity_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['auth_binding_id'] is None

    version_resp = await api_client.post(
        f'/api/v1/tools/{entity_id}/versions',
        json={
            'slug': 'send-email',
            'name': 'Send Email v2',
            'invocation_spec': {'method': 'POST', 'path': '/v2/email'},
        },
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2


@pytest.mark.asyncio
async def test_tool_data_binding_is_version_scoped(
    api_client, fake_principal, async_session
):
    from loom.model.datasource import DataSource
    from loom.model.enums import DataSourceKind

    datasource = DataSource(
        tenant_id=fake_principal.tenant_id,
        owner_id=fake_principal.principal_id,
        created_by_id=fake_principal.principal_id,
        slug='orders-db',
        name='Orders DB',
        kind=DataSourceKind.DATABASE,
    )
    async_session.add(datasource)
    await async_session.commit()

    create_resp = await api_client.post(
        '/api/v1/tools',
        json={'slug': 'query-orders', 'name': 'Query Orders', 'invocation_spec': {}},
    )
    entity_id = create_resp.json()['entity_id']

    binding_resp = await api_client.post(
        f'/api/v1/tools/{entity_id}/versions/1/data-bindings',
        json={'datasource_id': str(datasource.id), 'access_mode': 'read'},
    )
    assert binding_resp.status_code == 201

    list_resp = await api_client.get(
        f'/api/v1/tools/{entity_id}/versions/1/data-bindings'
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
