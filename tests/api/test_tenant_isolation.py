import uuid

import pytest
import pytest_asyncio

from loom.api.catalog.dependencies import get_current_principal, get_current_token
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.model.agent import Agent
from loom.model.enums import Layer, MemoryScope, PrincipalKind
from loom.model.tenant import Principal, Tenant

EVERY_SCOPE = ' '.join(sorted(content_scopes() | platform_scopes()))


@pytest_asyncio.fixture
async def other_tenant(async_session_factory) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a second Tenant plus one Principal, returning (tenant_id, principal_id)."""
    async with async_session_factory() as session:
        tenant = Tenant(slug='other-tenant', name='Other Tenant')
        session.add(tenant)
        await session.flush()
        principal = Principal(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            display_name='Other User',
            external_id='other-user',
        )
        session.add(principal)
        await session.commit()
        return tenant.id, principal.id


def _act_as(client, *, sub: str, tenant_id: uuid.UUID) -> None:
    """Re-point the client at an identity, letting principal resolution run for real."""
    client.app.dependency_overrides.pop(get_current_principal, None)
    client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': sub,
        'tenant_id': str(tenant_id),
        'scope': EVERY_SCOPE,
    }


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_an_entity_it_does_not_own(
    api_client, other_tenant
):
    create_resp = await api_client.post(
        '/api/v1/capabilities',
        json={'slug': 'private', 'name': 'Private', 'target_metrics': []},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    other_tenant_id, _ = other_tenant
    _act_as(api_client, sub='other-user', tenant_id=other_tenant_id)

    get_resp = await api_client.get(f'/api/v1/capabilities/{entity_id}')
    assert get_resp.status_code == 404

    versions_resp = await api_client.get(f'/api/v1/capabilities/{entity_id}/versions')
    assert versions_resp.status_code == 404

    list_resp = await api_client.get('/api/v1/capabilities')
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 0


@pytest.mark.asyncio
async def test_owner_id_from_another_tenant_is_rejected(api_client, other_tenant):
    _, other_principal_id = other_tenant

    resp = await api_client.post(
        '/api/v1/capabilities',
        json={
            'slug': 'stolen',
            'name': 'Stolen',
            'target_metrics': [],
            'owner_id': str(other_principal_id),
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_realization_referencing_another_tenants_agent_is_rejected(
    api_client, other_tenant, async_session
):
    other_tenant_id, other_principal_id = other_tenant
    foreign_agent = Agent(
        tenant_id=other_tenant_id,
        owner_id=other_principal_id,
        created_by_id=other_principal_id,
        slug='foreign-agent',
        name='Foreign Agent',
        layer=Layer.BUSINESS_OPS,
        llm_config={},
        prompt='p',
        memory_scope=MemoryScope.NONE,
    )
    async_session.add(foreign_agent)
    await async_session.commit()

    capability_resp = await api_client.post(
        '/api/v1/capabilities',
        json={'slug': 'mine', 'name': 'Mine', 'target_metrics': []},
    )
    entity_id = capability_resp.json()['entity_id']

    resp = await api_client.post(
        f'/api/v1/capabilities/{entity_id}/versions/1/realizations',
        json={
            'realizing_entity_type': 'agent',
            'realizing_agent_id': str(foreign_agent.id),
            'contribution_weight': '1.0',
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unprovisioned_principal_is_rejected_with_401(api_client, fake_principal):
    _act_as(api_client, sub='never-provisioned', tenant_id=fake_principal.tenant_id)

    resp = await api_client.get('/api/v1/capabilities')
    assert resp.status_code == 401
    assert 'principal' in resp.json()['detail'].lower()


@pytest.mark.asyncio
async def test_token_tenant_claim_must_be_a_uuid(api_client):
    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'test-user',
        'tenant_id': 'not-a-uuid',
        'scope': EVERY_SCOPE,
    }

    resp = await api_client.get('/api/v1/capabilities')
    assert resp.status_code == 401
