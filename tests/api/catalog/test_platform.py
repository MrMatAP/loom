import uuid

import pytest
import sqlalchemy as sa

from loom.api.catalog.dependencies import get_current_principal, get_current_token
from loom.http_headers import TENANT_HINT_HEADER
from loom.idp.catalog_roles import platform_scopes
from loom.model.enums import PrincipalKind
from loom.model.governance import AuditEvent
from loom.model.tenant import Principal, Tenant


@pytest.mark.asyncio
async def test_platform_admin_bootstraps_without_a_provisioned_principal(
    api_client, async_session
):
    """The whole point of the `catalog-platform-admin` role (see
    docs/admin-guide.md's "Platform administrator" section) is that it can
    create the very first Tenant/Principal in a fresh deployment, where by
    definition no Principal row -- for this caller or anyone else -- exists
    yet. Assert that directly: a token with only the platform scopes must
    still succeed, without `get_current_principal` (which would 401 on
    exactly this token, since no Principal row matches its `sub`) ever
    running -- `pop`, not override, so a bug that starts requiring
    principal resolution on these routes fails this test rather than
    silently passing through the fixture's override.

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
        '/api/v1/principals',
        json={
            'tenant_id': tenant_id,
            'kind': 'user',
            'external_id': 'ada@bootstrap-co.example',
        },
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
    """The common case (not bootstrap): a caller who already has a
    Principal row gets it recorded directly on the AuditEvent, with no
    need for the `details.actor_external_id` fallback."""
    create_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'acme', 'name': 'Acme Corp'}
    )
    assert create_resp.status_code == 201

    event = await async_session.scalar(
        sa.select(AuditEvent).where(AuditEvent.action == 'tenant.create')
    )
    assert event is not None
    assert event.actor_principal_id == fake_principal.principal_id
    assert event.details == {}


@pytest.mark.asyncio
async def test_tenant_hint_header_attributes_the_audit_event_correctly(
    api_client, async_session, async_session_factory
):
    """The `X-Loom-Tenant-Id` header that disambiguates a multi-tenant
    identity's auth (see tests/api/test_tenant_isolation.py) must also
    disambiguate its audit attribution -- `get_audit_actor` delegates to
    the exact same `resolve_principal` that gated the request, so this
    can't drift out of sync with what was actually authenticated."""
    async with async_session_factory() as session:
        first_tenant = Tenant(slug='first-hint-co', name='First Hint Co')
        second_tenant = Tenant(slug='second-hint-co', name='Second Hint Co')
        session.add_all([first_tenant, second_tenant])
        await session.flush()
        first_principal = Principal(
            tenant_id=first_tenant.id,
            kind=PrincipalKind.USER,
            external_id='hint-user',
        )
        session.add(first_principal)
        session.add(
            Principal(
                tenant_id=second_tenant.id,
                kind=PrincipalKind.USER,
                external_id='hint-user',
            )
        )
        await session.commit()
        first_principal_id = first_principal.id

    api_client.app.dependency_overrides.pop(get_current_principal, None)
    api_client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'hint-user',
        'scope': ' '.join(sorted(platform_scopes())),
    }
    api_client.headers[TENANT_HINT_HEADER] = str(first_tenant.id)

    create_resp = await api_client.post(
        '/api/v1/tenants', json={'slug': 'attributed', 'name': 'Attributed'}
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
            'external_id': 'ada@globex.example',
        },
    )
    assert create_resp.status_code == 201
    principal_id = create_resp.json()['id']

    get_resp = await api_client.get(f'/api/v1/principals/{principal_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['external_id'] == 'ada@globex.example'

    list_resp = await api_client.get(
        '/api/v1/principals', params={'tenant_id': tenant_id}
    )
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 1


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
