import uuid

import pytest
import pytest_asyncio

from loom.api.catalog.dependencies import get_current_principal, get_current_token
from loom.http_headers import TENANT_HINT_HEADER
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
            external_id='other-user',
        )
        session.add(principal)
        await session.commit()
        return tenant.id, principal.id


def _act_as(client, *, sub: str, tenant_hint: uuid.UUID | None = None) -> None:
    """Re-point the client at an identity, letting principal resolution run
    for real -- Tenant comes from whichever Principal row `sub` resolves
    to (`Principal.tenant_id`), not a token claim. `tenant_hint`, if
    given, is sent as the `X-Loom-Tenant-Id` header (see `loom auth
    set-tenant`) -- only needed to disambiguate an identity provisioned in
    more than one Tenant."""
    client.app.dependency_overrides.pop(get_current_principal, None)
    client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': sub,
        'scope': EVERY_SCOPE,
    }
    if tenant_hint is not None:
        client.headers[TENANT_HINT_HEADER] = str(tenant_hint)
    else:
        client.headers.pop(TENANT_HINT_HEADER, None)


@pytest_asyncio.fixture
async def multi_tenant_identity(async_session_factory) -> tuple[uuid.UUID, uuid.UUID]:
    """The same `sub` (`multi-tenant-user`) provisioned as a Principal in
    two different Tenants -- the legitimate case `X-Loom-Tenant-Id`
    disambiguates (`external_id` is only unique per Tenant, not globally;
    see `src/loom/model/tenant.py`'s `Principal` docstring). Returns
    (first_tenant_id, second_tenant_id)."""
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
    api_client, other_tenant
):
    create_resp = await api_client.post(
        '/api/v1/capabilities',
        json={'slug': 'private', 'name': 'Private', 'target_metrics': []},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    _act_as(api_client, sub='other-user')

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
async def test_unprovisioned_principal_is_rejected_with_401(api_client):
    _act_as(api_client, sub='never-provisioned')

    resp = await api_client.get('/api/v1/capabilities')
    assert resp.status_code == 401
    assert 'principal' in resp.json()['detail'].lower()


@pytest.mark.asyncio
async def test_identity_provisioned_in_multiple_tenants_is_rejected_with_401(
    api_client, multi_tenant_identity
):
    """Without an `X-Loom-Tenant-Id` header to disambiguate, an identity
    matching more than one Principal must fail closed rather than silently
    picking one -- see `resolve_principal` in
    `src/loom/api/catalog/dependencies.py`. The error also lists the
    candidate tenant_ids so the caller can self-service a `loom auth
    set-tenant <tenant_id>` without an admin's help."""
    first_tenant_id, second_tenant_id = multi_tenant_identity
    _act_as(api_client, sub='multi-tenant-user')

    resp = await api_client.get('/api/v1/capabilities')
    assert resp.status_code == 401
    detail = resp.json()['detail'].lower()
    assert 'ambiguous' in detail
    assert str(first_tenant_id) in detail
    assert str(second_tenant_id) in detail


@pytest.mark.asyncio
async def test_tenant_hint_disambiguates_a_multi_tenant_identity(
    api_client, multi_tenant_identity
):
    """`X-Loom-Tenant-Id` (set locally via `loom auth set-tenant`) picks
    which of the identity's own Principal rows to resolve to -- and each
    Tenant it picks is isolated from the other, same as any other Tenant."""
    first_tenant_id, second_tenant_id = multi_tenant_identity

    _act_as(api_client, sub='multi-tenant-user', tenant_hint=first_tenant_id)
    create_resp = await api_client.post(
        '/api/v1/capabilities',
        json={'slug': 'mine', 'name': 'Mine', 'target_metrics': []},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    _act_as(api_client, sub='multi-tenant-user', tenant_hint=second_tenant_id)
    get_resp = await api_client.get(f'/api/v1/capabilities/{entity_id}')
    assert get_resp.status_code == 404

    _act_as(api_client, sub='multi-tenant-user', tenant_hint=first_tenant_id)
    get_resp = await api_client.get(f'/api/v1/capabilities/{entity_id}')
    assert get_resp.status_code == 200


@pytest.mark.asyncio
async def test_tenant_hint_for_a_tenant_the_identity_lacks_is_rejected_with_401(
    api_client, multi_tenant_identity
):
    """A hint naming a real Tenant that this identity just isn't
    provisioned in -- e.g. a stale/copy-pasted `loom auth set-tenant`
    value -- must 401, not silently fall back to ambiguous-picks-one."""
    del multi_tenant_identity
    _act_as(api_client, sub='multi-tenant-user', tenant_hint=uuid.uuid4())

    resp = await api_client.get('/api/v1/capabilities')
    assert resp.status_code == 401
    assert 'no principal' in resp.json()['detail'].lower()


@pytest.mark.asyncio
async def test_tenant_hint_is_ignored_for_an_unambiguous_identity(api_client):
    """A hint is only consulted when it's actually needed to disambiguate
    -- an identity with exactly one Principal resolves normally even if a
    stale/incorrect hint is sent alongside it."""
    _act_as(api_client, sub='test-user', tenant_hint=uuid.uuid4())

    resp = await api_client.get('/api/v1/capabilities')
    assert resp.status_code == 200
