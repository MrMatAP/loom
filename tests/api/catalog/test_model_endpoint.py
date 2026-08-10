import pytest

from loom.model.enums import ModelProtocol
from loom.model.model_endpoint import ModelEndpoint
from loom.model.tenant import Principal, Tenant


@pytest.mark.asyncio
async def test_model_endpoint_full_lifecycle(api_client):
    create_resp = await api_client.post(
        '/api/v1/model-endpoints',
        json={
            'slug': 'self-hosted-llama',
            'name': 'Self-Hosted Llama',
            'protocol': 'openai_compatible',
            'base_url': 'https://llm.internal.example/v1',
            'model': 'meta-llama/Llama-3-70b',
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    entity_id = created['entity_id']
    assert created['protocol'] == 'openai_compatible'

    get_resp = await api_client.get(f'/api/v1/model-endpoints/{entity_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['model'] == 'meta-llama/Llama-3-70b'

    version_resp = await api_client.post(
        f'/api/v1/model-endpoints/{entity_id}/versions',
        json={
            'slug': 'self-hosted-llama',
            'name': 'Self-Hosted Llama v2',
            'protocol': 'openai_compatible',
            'base_url': 'https://llm.internal.example/v2',
            'model': 'meta-llama/Llama-3.1-70b',
        },
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2

    transition_resp = await api_client.post(
        f'/api/v1/model-endpoints/{entity_id}/versions/2/transitions',
        json={'to_state': 'in_review'},
    )
    assert transition_resp.status_code == 200


@pytest.mark.asyncio
async def test_model_endpoint_openai_compatible_requires_base_url(api_client):
    resp = await api_client.post(
        '/api/v1/model-endpoints',
        json={
            'slug': 'missing-base-url',
            'name': 'Missing Base URL',
            'protocol': 'openai_compatible',
            'model': 'some-model',
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_model_endpoint_not_found_returns_404(api_client):
    resp = await api_client.get(
        '/api/v1/model-endpoints/00000000-0000-0000-0000-000000000000'
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_can_bind_to_a_model_endpoint(api_client):
    endpoint_resp = await api_client.post(
        '/api/v1/model-endpoints',
        json={
            'slug': 'self-hosted-llama',
            'name': 'Self-Hosted Llama',
            'protocol': 'openai_compatible',
            'base_url': 'https://llm.internal.example/v1',
            'model': 'meta-llama/Llama-3-70b',
        },
    )
    assert endpoint_resp.status_code == 201
    # model_binding_id holds the entity's stable entity_id (floating), not a
    # specific version row's `id` -- there's no by-row-id lookup, so a `.id`
    # value could never be resolved back through the API.
    endpoint_entity_id = endpoint_resp.json()['entity_id']

    agent_resp = await api_client.post(
        '/api/v1/agents',
        json={
            'slug': 'triage-agent',
            'name': 'Triage Agent',
            'layer': 'business_ops',
            'model_binding_id': endpoint_entity_id,
            'llm_config': {'temperature': 0.2},
            'prompt': 'You triage tickets.',
            'memory_scope': 'session',
        },
    )
    assert agent_resp.status_code == 201
    assert agent_resp.json()['model_binding_id'] == endpoint_entity_id

    # Resolvable: a client holding only the Agent can look up what it's
    # bound to through the same endpoint used to create it.
    lookup_resp = await api_client.get(
        f'/api/v1/model-endpoints/{agent_resp.json()["model_binding_id"]}'
    )
    assert lookup_resp.status_code == 200
    assert lookup_resp.json()['model'] == 'meta-llama/Llama-3-70b'


@pytest.mark.asyncio
async def test_agent_model_binding_floats_to_the_new_current_version(api_client):
    endpoint_resp = await api_client.post(
        '/api/v1/model-endpoints',
        json={
            'slug': 'self-hosted-llama',
            'name': 'Self-Hosted Llama',
            'protocol': 'openai_compatible',
            'base_url': 'https://llm.internal.example/v1',
            'model': 'meta-llama/Llama-3-70b',
        },
    )
    endpoint_entity_id = endpoint_resp.json()['entity_id']

    agent_resp = await api_client.post(
        '/api/v1/agents',
        json={
            'slug': 'triage-agent',
            'name': 'Triage Agent',
            'layer': 'business_ops',
            'model_binding_id': endpoint_entity_id,
            'llm_config': {},
            'prompt': 'You triage tickets.',
            'memory_scope': 'session',
        },
    )
    assert agent_resp.status_code == 201

    version_resp = await api_client.post(
        f'/api/v1/model-endpoints/{endpoint_entity_id}/versions',
        json={
            'slug': 'self-hosted-llama',
            'name': 'Self-Hosted Llama v2',
            'protocol': 'openai_compatible',
            'base_url': 'https://llm.internal.example/v2',
            'model': 'meta-llama/Llama-3.1-70b',
        },
    )
    assert version_resp.status_code == 201

    # Same model_binding_id, now resolves to the new current version.
    lookup_resp = await api_client.get(f'/api/v1/model-endpoints/{endpoint_entity_id}')
    assert lookup_resp.json()['model'] == 'meta-llama/Llama-3.1-70b'


@pytest.mark.asyncio
async def test_agent_rejects_model_binding_from_another_tenant(
    api_client, async_session_factory
):
    async with async_session_factory() as session:
        other_tenant = Tenant(slug='other', name='Other Tenant')
        session.add(other_tenant)
        await session.flush()
        other_principal = Principal(
            tenant_id=other_tenant.id,
            kind='user',
            display_name='Bob',
            external_id='bob',
        )
        session.add(other_principal)
        await session.flush()
        foreign_endpoint = ModelEndpoint(
            tenant_id=other_tenant.id,
            owner_id=other_principal.id,
            created_by_id=other_principal.id,
            slug='foreign-endpoint',
            name='Foreign Endpoint',
            protocol=ModelProtocol.OPENAI_COMPATIBLE,
            base_url='https://llm.example/v1',
            model='some-model',
        )
        session.add(foreign_endpoint)
        await session.commit()
        foreign_entity_id = foreign_endpoint.entity_id

    agent_resp = await api_client.post(
        '/api/v1/agents',
        json={
            'slug': 'triage-agent',
            'name': 'Triage Agent',
            'layer': 'business_ops',
            'model_binding_id': str(foreign_entity_id),
            'llm_config': {},
            'prompt': 'You triage tickets.',
            'memory_scope': 'session',
        },
    )
    assert agent_resp.status_code == 404
