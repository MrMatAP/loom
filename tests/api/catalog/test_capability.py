import pytest

from loom.domain.enums import Layer, MemoryScope, RealizingEntityType
from loom.persistence.agent import Agent


@pytest.mark.asyncio
async def test_capability_full_lifecycle(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities',
        json={'name': 'Ticket Triage', 'target_metrics': []},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    entity_id = created['entity_id']
    assert created['version'] == 1
    assert created['lifecycle_state'] == 'draft'

    get_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}'
    )
    assert get_resp.status_code == 200
    assert get_resp.json()['name'] == 'Ticket Triage'

    list_resp = await api_client.get(f'/api/v1/tenants/{tenant_id}/capabilities')
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body['total'] == 1
    assert body['items'][0]['entity_id'] == entity_id

    version_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions',
        json={'name': 'Ticket Triage v2', 'target_metrics': []},
    )
    assert version_resp.status_code == 201
    v2 = version_resp.json()
    assert v2['version'] == 2
    assert v2['lifecycle_state'] == 'draft'

    versions_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions'
    )
    assert versions_resp.status_code == 200
    assert len(versions_resp.json()) == 2

    old_version_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions/1'
    )
    assert old_version_resp.status_code == 200
    assert old_version_resp.json()['name'] == 'Ticket Triage'

    current_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}'
    )
    assert current_resp.json()['version'] == 2

    transition_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions/2/transitions',
        json={'to_state': 'in_review'},
    )
    assert transition_resp.status_code == 200
    assert transition_resp.json()['lifecycle_state'] == 'in_review'


@pytest.mark.asyncio
async def test_illegal_transition_returns_409(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities',
        json={'name': 'Skip Ahead', 'target_metrics': []},
    )
    entity_id = create_resp.json()['entity_id']

    resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions/1/transitions',
        json={'to_state': 'published'},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_transition_on_stale_version_returns_409(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities',
        json={'name': 'Stale', 'target_metrics': []},
    )
    entity_id = create_resp.json()['entity_id']
    await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions',
        json={'name': 'Stale v2', 'target_metrics': []},
    )

    resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions/1/transitions',
        json={'to_state': 'in_review'},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_capability_not_found_returns_404(api_client, fake_principal):
    resp = await api_client.get(
        f'/api/v1/tenants/{fake_principal.tenant_id}/capabilities/'
        '00000000-0000-0000-0000-000000000000'
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_realizations_are_version_scoped(
    api_client, fake_principal, async_session
):
    tenant_id = fake_principal.tenant_id
    agent = Agent(
        tenant_id=fake_principal.tenant_id,
        owner_id=fake_principal.principal_id,
        created_by_id=fake_principal.principal_id,
        name='Triage Agent',
        layer=Layer.BUSINESS_OPS,
        llm_config={},
        prompt='p',
        memory_scope=MemoryScope.NONE,
    )
    async_session.add(agent)
    await async_session.commit()

    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities',
        json={'name': 'Realize Me', 'target_metrics': []},
    )
    entity_id = create_resp.json()['entity_id']

    add_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions/1/realizations',
        json={
            'realizing_entity_type': RealizingEntityType.AGENT.value,
            'realizing_agent_id': str(agent.id),
            'contribution_weight': '1.0',
        },
    )
    assert add_resp.status_code == 201

    list_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions/1/realizations'
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


@pytest.mark.asyncio
async def test_missing_scope_returns_403(api_client, fake_principal):
    from loom.api.catalog.dependencies import get_current_token

    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'test-user',
        'scope': 'catalog:capability:read',
    }
    resp = await api_client.post(
        f'/api/v1/tenants/{fake_principal.tenant_id}/capabilities',
        json={'name': 'Nope', 'target_metrics': []},
    )
    assert resp.status_code == 403
