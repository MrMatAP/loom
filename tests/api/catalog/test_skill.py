import pytest


@pytest.mark.asyncio
async def test_atomic_skill_requires_content_returns_422(api_client, fake_principal):
    resp = await api_client.post(
        f'/api/v1/tenants/{fake_principal.tenant_id}/skills',
        json={
            'name': 'Draft Reply',
            'layer': 'business_ops',
            'kind': 'atomic',
            'atomic_content': None,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_composite_skill_graph_lifecycle(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills',
        json={
            'name': 'Triage Flow',
            'layer': 'business_ops',
            'kind': 'composite',
            'is_entry_point': True,
        },
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    agent_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/agents',
        json={
            'name': 'Triage Agent',
            'layer': 'business_ops',
            'llm_config': {},
            'prompt': 'p',
            'memory_scope': 'none',
        },
    )
    agent_version_id = agent_resp.json()['id']

    node_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions/1/nodes',
        json={'node_key': 'start', 'node_type': 'agent', 'agent_id': agent_version_id},
    )
    assert node_resp.status_code == 201
    node_id = node_resp.json()['id']

    nodes_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions/1/nodes'
    )
    assert len(nodes_resp.json()) == 1

    edge_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions/1/edges',
        json={'from_node_id': node_id, 'to_node_id': node_id},
    )
    assert edge_resp.status_code == 201

    edges_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions/1/edges'
    )
    assert len(edges_resp.json()) == 1

    transition_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions/1/transitions',
        json={'to_state': 'in_review'},
    )
    assert transition_resp.status_code == 200


async def _create_composite_skill(api_client, tenant_id, name: str) -> str:
    resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills',
        json={
            'name': name,
            'layer': 'business_ops',
            'kind': 'composite',
        },
    )
    assert resp.status_code == 201
    return resp.json()['entity_id']


@pytest.mark.asyncio
async def test_edge_endpoints_must_belong_to_the_same_skill_version(
    api_client, fake_principal
):
    tenant_id = fake_principal.tenant_id
    agent_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/agents',
        json={
            'name': 'Edge Agent',
            'layer': 'business_ops',
            'llm_config': {},
            'prompt': 'p',
            'memory_scope': 'none',
        },
    )
    agent_version_id = agent_resp.json()['id']

    host_id = await _create_composite_skill(api_client, tenant_id, 'host-flow')
    other_id = await _create_composite_skill(api_client, tenant_id, 'other-flow')

    host_node_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{host_id}/versions/1/nodes',
        json={'node_key': 'a', 'node_type': 'agent', 'agent_id': agent_version_id},
    )
    host_node_id = host_node_resp.json()['id']

    foreign_node_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{other_id}/versions/1/nodes',
        json={'node_key': 'b', 'node_type': 'agent', 'agent_id': agent_version_id},
    )
    foreign_node_id = foreign_node_resp.json()['id']

    resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{host_id}/versions/1/edges',
        json={'from_node_id': host_node_id, 'to_node_id': foreign_node_id},
    )
    assert resp.status_code == 404

    resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{host_id}/versions/1/edges',
        json={'from_node_id': foreign_node_id, 'to_node_id': host_node_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_edge_on_a_different_version_of_the_same_skill_is_rejected(
    api_client, fake_principal
):
    tenant_id = fake_principal.tenant_id
    entity_id = await _create_composite_skill(api_client, tenant_id, 'versioned-flow')
    agent_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/agents',
        json={
            'name': 'V Agent',
            'layer': 'business_ops',
            'llm_config': {},
            'prompt': 'p',
            'memory_scope': 'none',
        },
    )
    agent_version_id = agent_resp.json()['id']

    v1_node_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions/1/nodes',
        json={'node_key': 'a', 'node_type': 'agent', 'agent_id': agent_version_id},
    )
    v1_node_id = v1_node_resp.json()['id']

    version_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions',
        json={
            'name': 'versioned-flow v2',
            'layer': 'business_ops',
            'kind': 'composite',
        },
    )
    assert version_resp.status_code == 201

    resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/skills/{entity_id}/versions/2/edges',
        json={'from_node_id': v1_node_id, 'to_node_id': v1_node_id},
    )
    assert resp.status_code == 404
