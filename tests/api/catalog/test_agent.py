import pytest


@pytest.mark.asyncio
async def test_agent_full_lifecycle(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/agents',
        json={
            'name': 'Triage Agent',
            'layer': 'business_ops',
            'llm_config': {'temperature': 0.2},
            'prompt': 'You triage tickets.',
            'memory_scope': 'session',
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    entity_id = created['entity_id']
    assert created['version'] == 1
    assert created['permission_boundary'] == {}

    get_resp = await api_client.get(f'/api/v1/tenants/{tenant_id}/agents/{entity_id}')
    assert get_resp.status_code == 200

    list_resp = await api_client.get(f'/api/v1/tenants/{tenant_id}/agents')
    assert list_resp.json()['total'] == 1

    version_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/agents/{entity_id}/versions',
        json={
            'name': 'Triage Agent v2',
            'layer': 'business_ops',
            'llm_config': {'temperature': 0.2},
            'prompt': 'You triage tickets, v2.',
            'memory_scope': 'session',
        },
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2

    transition_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/agents/{entity_id}/versions/2/transitions',
        json={'to_state': 'in_review'},
    )
    assert transition_resp.status_code == 200
    assert transition_resp.json()['lifecycle_state'] == 'in_review'


@pytest.mark.asyncio
async def test_agent_not_found_returns_404(api_client, fake_principal):
    resp = await api_client.get(
        f'/api/v1/tenants/{fake_principal.tenant_id}/agents/'
        '00000000-0000-0000-0000-000000000000'
    )
    assert resp.status_code == 404
