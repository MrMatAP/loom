import uuid

import pytest
import sqlalchemy as sa

from loom.api.catalog.dependencies import get_current_principal, get_current_token
from loom.domain.enums import PrincipalKind
from loom.idp.catalog_roles import ROLE_BUNDLES, content_scopes, platform_scopes
from loom.persistence.governance import AuditEvent
from loom.persistence.tenant import Principal, Tenant


@pytest.mark.asyncio
async def test_platform_admin_bootstraps_without_a_provisioned_principal(
    api_client, async_session
):
    """The whole point of the `catalog-platform-admin` role (see
    docs/admin-guide.md's "Platform administrator" section) is that it can
    create the very first Tenant/Principal in a fresh deployment, where by
    definition no Principal row -- for this caller or anyone else -- exists
    yet. Assert that directly: a token with only the platform scopes must
    still succeed, without `get_current_principal` (which would 403 on
    exactly this token, since no Principal row matches its `sub` in the
    target Tenant) ever running -- `pop`, not override, so a bug that
    starts requiring principal resolution on these routes fails this test
    rather than silently passing through the fixture's override.

    Also asserts the bootstrap is still audited even though there's no
    Principal to attribute it to (see `src/loom/api/catalog/audit.py`) --
    losing the audit trail was the whole reason `loom db seed-principal`
    (which wrote one) couldn't just be deleted without a replacement."""
    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'platform-admin-user',
        'scope': ' '.join(sorted(platform_scopes())),
    }

    tenant_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'bootstrap-co', 'name': 'Bootstrap Co'}
    )
    assert tenant_resp.status_code == 201
    tenant_id = tenant_resp.json()['id']

    principal_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/principals',
        json={'kind': 'user', 'external_id': 'ada@bootstrap-co.example'},
    )
    assert principal_resp.status_code == 201
    principal_id = principal_resp.json()['id']

    events = (
        await async_session.scalars(sa.select(AuditEvent).order_by(AuditEvent.action))
    ).all()
    assert {(e.action, e.entity_id) for e in events} == {
        ('principal.create', uuid.UUID(principal_id)),
        ('tenant.create', uuid.UUID(tenant_id)),
    }
    for event in events:
        assert event.actor_principal_id is None
        assert event.details == {'actor_external_id': 'platform-admin-user'}
        assert event.decision.value == 'allow'


@pytest.mark.asyncio
async def test_tenant_create_by_a_provisioned_principal_attributes_the_audit_event(
    api_client, async_session, fake_principal
):
    """`POST /tenants` has no Tenant of its own in its URL path (it's
    creating one), so audit attribution here (`get_audit_actor_for_new_
    tenant`) is always via `details.actor_external_id`, never a
    Principal -- even for a caller who already has one elsewhere. Creating
    a Tenant is inherently a cross-Tenant, platform-admin action; see
    `src/loom/api/catalog/audit.py`."""
    create_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'acme', 'name': 'Acme Corp'}
    )
    assert create_resp.status_code == 201

    del fake_principal  # kept unused: proves the *default* fixture's Principal is
    # still ignored for this route, not just absent
    event = await async_session.scalar(
        sa.select(AuditEvent).where(AuditEvent.action == 'tenant.create')
    )
    assert event is not None
    assert event.actor_principal_id is None
    assert event.details == {'actor_external_id': 'test-user'}


@pytest.mark.asyncio
async def test_principal_create_attributes_the_audit_event_to_the_path_tenant(
    api_client, async_session, async_session_factory
):
    """`POST /tenants/{tenant_id}/principals`'s audit attribution
    (`get_audit_actor`) must resolve the caller's Principal in exactly the
    Tenant named by the URL path -- not any other Tenant the same `sub`
    happens to also have a Principal in. Provisioning the identity in two
    Tenants and creating in each proves attribution tracks the path, not
    just "some Principal for this sub"."""
    async with async_session_factory() as session:
        first_tenant = Tenant(slug='first-audit-co', name='First Audit Co')
        second_tenant = Tenant(slug='second-audit-co', name='Second Audit Co')
        session.add_all([first_tenant, second_tenant])
        await session.flush()
        first_principal = Principal(
            tenant_id=first_tenant.id, kind=PrincipalKind.USER, external_id='audit-user'
        )
        session.add(first_principal)
        session.add(
            Principal(
                tenant_id=second_tenant.id,
                kind=PrincipalKind.USER,
                external_id='audit-user',
            )
        )
        await session.commit()
        first_tenant_id, first_principal_id = first_tenant.id, first_principal.id

    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'audit-user',
        'scope': ' '.join(sorted(platform_scopes())),
    }

    create_resp = await api_client.post(
        f'/api/v1/tenants/{first_tenant_id}/principals',
        json={'kind': 'user', 'external_id': 'someone-else@first-audit-co.example'},
    )
    assert create_resp.status_code == 201

    event = await async_session.scalar(
        sa.select(AuditEvent).where(
            AuditEvent.entity_id == uuid.UUID(create_resp.json()['id'])
        )
    )
    assert event is not None
    assert event.actor_principal_id == first_principal_id
    assert event.details == {}


@pytest.mark.asyncio
async def test_list_my_tenants_returns_every_tenant_for_a_platform_admin(
    api_client, async_session_factory
):
    """`GET /tenants/mine` backs `loom auth login`'s tenant-selection step
    (`src/loom/cli/auth.py`'s `_select_tenant`). A platform admin
    (`catalog:tenant:read` in scope) may have zero Principals of their own
    -- it must still show every Tenant, not just ones they happen to be
    provisioned in (see docs/admin-guide.md's "Platform administrator"
    section), and must work without `get_current_principal` ever running
    (`pop`, not override, so a bug that starts requiring principal
    resolution on this route fails this test).

    The "sees every Tenant" branch is gated purely on `catalog:tenant:read`
    being in scope, not on a `catalog-platform-admin`-specific check --
    pin that only `catalog-platform-admin` currently carries it, so an
    edit to `ROLE_BUNDLES` that grants it to a narrower role fails *this*
    test (which names the endpoint it would silently widen) instead of
    going unnoticed."""
    assert 'catalog:tenant:read' in ROLE_BUNDLES['catalog-platform-admin']
    assert not any(
        'catalog:tenant:read' in scopes
        for role, scopes in ROLE_BUNDLES.items()
        if role != 'catalog-platform-admin'
    )

    async with async_session_factory() as session:
        session.add_all(
            [Tenant(slug='mine-a', name='Mine A'), Tenant(slug='mine-b', name='Mine B')]
        )
        await session.commit()

    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'admin-with-no-principal',
        'scope': ' '.join(sorted(platform_scopes())),
    }

    resp = await api_client.get('/api/v1/tenants/mine')
    assert resp.status_code == 200
    slugs = {t['slug'] for t in resp.json()['items']}
    assert {'mine-a', 'mine-b'}.issubset(slugs)


@pytest.mark.asyncio
async def test_list_my_tenants_is_scoped_to_the_callers_own_tenants_for_a_regular_user(
    api_client, async_session_factory
):
    """No `catalog:tenant:read` scope -- a regular (non-platform-admin)
    caller sees only the Tenants where they already have a Principal,
    never another Tenant's, even one that exists in the same deployment."""
    async with async_session_factory() as session:
        own_tenant = Tenant(slug='own-tenant', name='Own Tenant')
        other_tenant = Tenant(slug='other-tenant', name='Other Tenant')
        session.add_all([own_tenant, other_tenant])
        await session.flush()
        session.add(
            Principal(
                tenant_id=own_tenant.id,
                kind=PrincipalKind.USER,
                external_id='mine-user',
            )
        )
        await session.commit()

    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'mine-user',
        'scope': ' '.join(sorted(content_scopes())),
    }

    resp = await api_client.get('/api/v1/tenants/mine')
    assert resp.status_code == 200
    assert {t['slug'] for t in resp.json()['items']} == {'own-tenant'}


@pytest.mark.asyncio
async def test_list_my_tenants_is_empty_for_an_unprovisioned_regular_user(api_client):
    """Zero Principals and no platform scope -- an empty list, not a 401,
    since this endpoint exists precisely to let a caller discover they
    have nothing yet (see `_select_tenant`'s "ask your admin" message)."""
    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'nobody-yet',
        'scope': ' '.join(sorted(content_scopes())),
    }

    resp = await api_client.get('/api/v1/tenants/mine')
    assert resp.status_code == 200
    assert resp.json()['items'] == []


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
        f'/api/v1/tenants/{tenant_id}/principals',
        json={'kind': 'user', 'external_id': 'ada@globex.example'},
    )
    assert create_resp.status_code == 201
    principal_id = create_resp.json()['id']

    get_resp = await api_client.get(
        f'/api/v1/tenants/{tenant_id}/principals/{principal_id}'
    )
    assert get_resp.status_code == 200
    assert get_resp.json()['external_id'] == 'ada@globex.example'

    list_resp = await api_client.get(f'/api/v1/tenants/{tenant_id}/principals')
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 1


@pytest.mark.asyncio
async def test_principal_get_is_scoped_to_the_path_tenant(
    api_client, async_session_factory
):
    """A Principal from Tenant A must 404 when looked up through Tenant
    B's path, even with every scope -- closes a latent gap where `GET
    /principals/{id}` never checked tenant membership at all before this
    endpoint gained a `tenant_id` path segment."""
    async with async_session_factory() as session:
        owning_tenant = Tenant(slug='owning-co', name='Owning Co')
        other_tenant = Tenant(slug='other-co-2', name='Other Co 2')
        session.add_all([owning_tenant, other_tenant])
        await session.flush()
        principal = Principal(
            tenant_id=owning_tenant.id, kind=PrincipalKind.USER, external_id='carl'
        )
        session.add(principal)
        await session.commit()
        owning_tenant_id, other_tenant_id = owning_tenant.id, other_tenant.id
        principal_id = principal.id

    resp = await api_client.get(
        f'/api/v1/tenants/{other_tenant_id}/principals/{principal_id}'
    )
    assert resp.status_code == 404

    resp = await api_client.get(
        f'/api/v1/tenants/{owning_tenant_id}/principals/{principal_id}'
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_environment_crud(api_client):
    tenant_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'initech', 'name': 'Initech'}
    )
    tenant_id = tenant_resp.json()['id']

    create_resp = await api_client.post(
        f'/api/v1/tenants/{tenant_id}/environments',
        json={
            'name': 'prod',
            'kind': 'production',
            'compute_boundary_ref': 'vpc-prod-compute',
            'network_boundary_ref': 'vpc-prod-net',
        },
    )
    assert create_resp.status_code == 201
    environment_id = create_resp.json()['id']

    list_resp = await api_client.get(f'/api/v1/tenants/{tenant_id}/environments')
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 1

    update_resp = await api_client.patch(
        f'/api/v1/tenants/{tenant_id}/environments/{environment_id}',
        json={'compute_boundary_ref': 'vpc-prod-compute-v2'},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()['compute_boundary_ref'] == 'vpc-prod-compute-v2'
