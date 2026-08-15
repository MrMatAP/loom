import pytest

from loom.api.catalog.dependencies import get_current_token
from loom.idp.catalog_roles import content_scopes, platform_scopes

ALL_SCOPES = content_scopes() | platform_scopes()

# Path suffixes (after `/api/v1/tenants/{tenant_id}`) and the scope each
# route's read/write should require -- built against the real
# `fake_principal.tenant_id` inside the test, not a module-level constant,
# since every one of these (Tenant's own two aside) now needs a real,
# resolvable Tenant in the URL path for the "full access succeeds" half of
# each read test to mean anything.
READ_ROUTES = [
    ('/capabilities', 'catalog:capability:read'),
    ('/agents', 'catalog:agent:read'),
    ('/skills', 'catalog:skill:read'),
    ('/tools', 'catalog:tool:read'),
    ('/datasources', 'catalog:datasource:read'),
    ('/dataproducts', 'catalog:dataproduct:read'),
    ('/principals', 'catalog:principal:read'),
    ('/environments', 'catalog:environment:read'),
]

WRITE_ROUTES = [
    (
        '/capabilities',
        'catalog:capability:write',
        {'name': 'n', 'target_metrics': []},
    ),
    (
        '/agents',
        'catalog:agent:write',
        {
            'name': 'n',
            'layer': 'business_ops',
            'llm_config': {},
            'prompt': 'p',
            'memory_scope': 'none',
        },
    ),
    (
        '/skills',
        'catalog:skill:write',
        {'name': 'n', 'layer': 'business_ops', 'kind': 'composite'},
    ),
    (
        '/tools',
        'catalog:tool:write',
        {'name': 'n', 'invocation_spec': {}},
    ),
    (
        '/datasources',
        'catalog:datasource:write',
        {'name': 'n', 'kind': 'database'},
    ),
    (
        '/dataproducts',
        'catalog:dataproduct:write',
        {'name': 'n', 'contract': {}},
    ),
    (
        '/principals',
        'catalog:principal:write',
        {'kind': 'user', 'external_id': 'x'},
    ),
    (
        '/environments',
        'catalog:environment:write',
        {
            'name': 'n',
            'kind': 'sandbox',
            'compute_boundary_ref': 'c',
            'network_boundary_ref': 'nw',
        },
    ),
]


def _withhold(client, scope: str, fake_principal) -> None:
    """Grant every scope except `scope`, so only that one can cause a 403."""
    granted = ' '.join(sorted(ALL_SCOPES - {scope}))
    client.app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'test-user',
        'scope': granted,
    }


@pytest.mark.parametrize(('suffix', 'scope'), READ_ROUTES)
@pytest.mark.asyncio
async def test_read_route_requires_its_own_scope(
    api_client, fake_principal, suffix, scope
):
    path = f'/api/v1/tenants/{fake_principal.tenant_id}{suffix}'
    assert (await api_client.get(path)).status_code == 200

    _withhold(api_client, scope, fake_principal)
    resp = await api_client.get(path)
    assert resp.status_code == 403
    assert scope in resp.json()['detail']


@pytest.mark.asyncio
async def test_tenant_read_requires_its_own_scope(api_client, fake_principal):
    assert (await api_client.get('/api/v1/tenants')).status_code == 200

    _withhold(api_client, 'catalog:tenant:read', fake_principal)
    resp = await api_client.get('/api/v1/tenants')
    assert resp.status_code == 403
    assert 'catalog:tenant:read' in resp.json()['detail']


@pytest.mark.parametrize(('suffix', 'scope', 'body'), WRITE_ROUTES)
@pytest.mark.asyncio
async def test_write_route_requires_its_own_scope(
    api_client, fake_principal, suffix, scope, body
):
    path = f'/api/v1/tenants/{fake_principal.tenant_id}{suffix}'
    _withhold(api_client, scope, fake_principal)
    resp = await api_client.post(path, json=body)
    assert resp.status_code == 403
    assert scope in resp.json()['detail']


@pytest.mark.asyncio
async def test_tenant_write_requires_its_own_scope(api_client, fake_principal):
    _withhold(api_client, 'catalog:tenant:write', fake_principal)
    resp = await api_client.post('/api/v1/tenants', json={'slug': 's', 'name': 'n'})
    assert resp.status_code == 403
    assert 'catalog:tenant:write' in resp.json()['detail']
