import uuid

import pytest
import pytest_asyncio

from loom.api.catalog.dependencies import get_current_principal, get_current_token
from loom.domain.enums import Layer, MemoryScope, PrincipalKind
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.persistence.agent import Agent
from loom.persistence.tenant import Principal, Tenant

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
            external_id='other-user',
        )
        session.add(principal)
        await session.commit()
        return tenant.id, principal.id


def _act_as(client, *, sub: str) -> None:
    """Re-point the client at an identity, letting principal resolution run
    for real -- `get_current_principal` looks the caller's Principal up in
    exactly the Tenant named by the request's own URL path
    (`/tenants/{tenant_id}/...`), not a token claim or header hint."""
    client.app.dependency_overrides.pop(get_current_principal, None)
    client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': sub,
        'scope': EVERY_SCOPE,
    }


@pytest_asyncio.fixture
async def multi_tenant_identity(async_session_factory) -> tuple[uuid.UUID, uuid.UUID]:
    """The same `sub` (`multi-tenant-user`) provisioned as a Principal in
    two different Tenants -- the legitimate case a caller disambiguates by
    which Tenant they put in the URL path (`external_id` is only unique
    per Tenant, not globally; see `src/loom/model/tenant.py`'s `Principal`
    docstring). Returns (first_tenant_id, second_tenant_id)."""
    async with async_session_factory() as session:
        first_tenant = Tenant(slug='first-tenant', name='First Tenant')
        second_tenant = Tenant(slug='second-tenant', name='Second Tenant')
        session.add_all([first_tenant, second_tenant])
        await session.flush()
        session.add_all(
            [
                Principal(
                    tenant_id=first_tenant.id,
                    kind=PrincipalKind.USER,
                    external_id='multi-tenant-user',
                ),
                Principal(
                    tenant_id=second_tenant.id,
                    kind=PrincipalKind.USER,
                    external_id='multi-tenant-user',
                ),
            ]
        )
        await session.commit()
        return first_tenant.id, second_tenant.id


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_an_entity_it_does_not_own(
    api_client, fake_principal, other_tenant
):
    tenant_id = fake_principal.tenant_id
    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities',
        json={'name': 'Private', 'target_metrics': []},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    other_tenant_id, _ = other_tenant
    _act_as(api_client, sub='other-user')

    get_resp = await api_client.get(
        f'/api/v1/tenants/{other_tenant_id}/capabilities/{entity_id}'
    )
    assert get_resp.status_code == 404

    versions_resp = await api_client.get(
        f'/api/v1/tenants/{other_tenant_id}/capabilities/{entity_id}/versions'
    )
    assert versions_resp.status_code == 404

    list_resp = await api_client.get(f'/api/v1/tenants/{other_tenant_id}/capabilities')
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 0


@pytest.mark.asyncio
async def test_realization_referencing_another_tenants_agent_is_rejected(
    api_client, fake_principal, other_tenant, async_session
):
    tenant_id = fake_principal.tenant_id
    other_tenant_id, other_principal_id = other_tenant
    foreign_agent = Agent(
        tenant_id=other_tenant_id,
        owner_id=other_principal_id,
        created_by_id=other_principal_id,
        name='Foreign Agent',
        layer=Layer.BUSINESS_OPS,
        llm_config={},
        prompt='p',
        memory_scope=MemoryScope.NONE,
    )
    async_session.add(foreign_agent)
    await async_session.commit()

    capability_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities',
        json={'name': 'Mine', 'target_metrics': []},
    )
    entity_id = capability_resp.json()['entity_id']

    resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/capabilities/{entity_id}/versions/1/realizations',
        json={
            'realizing_entity_type': 'agent',
            'realizing_agent_id': str(foreign_agent.id),
            'contribution_weight': '1.0',
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unprovisioned_principal_is_rejected_with_403(api_client, other_tenant):
    """Token valid, but no Principal in the Tenant the path names -- the
    fix isn't re-authenticating (that's 401), it's provisioning, so this
    is 403 (see `PrincipalNotInTenantError`)."""
    other_tenant_id, _ = other_tenant
    _act_as(api_client, sub='never-provisioned')

    resp = await api_client.get(f'/api/v1/tenants/{other_tenant_id}/capabilities')
    assert resp.status_code == 403
    assert 'no principal' in resp.json()['detail'].lower()


@pytest.mark.asyncio
async def test_platform_admin_scope_does_not_substitute_for_tenant_membership(
    api_client, other_tenant
):
    """Full platform scopes (`catalog:*:*`) authorize the *route*, but
    content-tier endpoints (unlike `GET/PATCH /tenants/{id}`) still require
    a Principal in the path Tenant -- a platform admin with no Principal
    row anywhere must 403 here, the same as any other unprovisioned
    identity (`test_unprovisioned_principal_is_rejected_with_403`), not
    get treated as implicitly a member of every Tenant."""
    other_tenant_id, _ = other_tenant
    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'platform-admin-no-principal',
        'scope': ' '.join(sorted(platform_scopes())),
    }

    resp = await api_client.get(f'/api/v1/tenants/{other_tenant_id}/capabilities')
    assert resp.status_code == 403
    assert 'no principal' in resp.json()['detail'].lower()


@pytest.mark.asyncio
async def test_multi_tenant_identity_is_isolated_per_tenant_in_the_path(
    api_client, multi_tenant_identity
):
    """A Principal provisioned in two Tenants reaches each one by putting
    it in the URL path -- and each is isolated from the other, same as
    any other Tenant pair."""
    first_tenant_id, second_tenant_id = multi_tenant_identity
    _act_as(api_client, sub='multi-tenant-user')

    create_resp = await api_client.post(
        f'/api/v1/tenants/{first_tenant_id}/capabilities',
        json={'name': 'Mine', 'target_metrics': []},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    get_resp = await api_client.get(
        f'/api/v1/tenants/{second_tenant_id}/capabilities/{entity_id}'
    )
    assert get_resp.status_code == 404

    get_resp = await api_client.get(
        f'/api/v1/tenants/{first_tenant_id}/capabilities/{entity_id}'
    )
    assert get_resp.status_code == 200


@pytest.mark.asyncio
async def test_path_naming_a_tenant_the_identity_lacks_is_rejected_with_403(
    api_client, multi_tenant_identity
):
    """A path naming a real Tenant this identity just isn't provisioned in
    -- e.g. a stale/copy-pasted `loom auth set-tenant` value -- must 403,
    not silently fall back to one of the identity's other Tenants."""
    del multi_tenant_identity
    _act_as(api_client, sub='multi-tenant-user')

    resp = await api_client.get(f'/api/v1/tenants/{uuid.uuid4()}/capabilities')
    assert resp.status_code == 403
    assert 'no principal' in resp.json()['detail'].lower()
