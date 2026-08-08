import uuid

import pytest

from loom.api.catalog.dependencies import get_current_token
from loom.idp.catalog_roles import content_scopes, platform_scopes

ALL_SCOPES = content_scopes() | platform_scopes()
ANY_TENANT = str(uuid.uuid4())

READ_ROUTES = [
    ('/api/v1/capabilities', 'catalog:capability:read'),
    ('/api/v1/agents', 'catalog:agent:read'),
    ('/api/v1/skills', 'catalog:skill:read'),
    ('/api/v1/tools', 'catalog:tool:read'),
    ('/api/v1/datasources', 'catalog:datasource:read'),
    ('/api/v1/dataproducts', 'catalog:dataproduct:read'),
    ('/api/v1/tenants', 'catalog:tenant:read'),
    (f'/api/v1/principals?tenant_id={ANY_TENANT}', 'catalog:principal:read'),
    (f'/api/v1/environments?tenant_id={ANY_TENANT}', 'catalog:environment:read'),
]

WRITE_ROUTES = [
    (
        '/api/v1/capabilities',
        'catalog:capability:write',
        {'slug': 's', 'name': 'n', 'target_metrics': []},
    ),
    (
        '/api/v1/agents',
        'catalog:agent:write',
        {
            'slug': 's',
            'name': 'n',
            'layer': 'business_ops',
            'llm_config': {},
            'prompt': 'p',
            'memory_scope': 'none',
        },
    ),
    (
        '/api/v1/skills',
        'catalog:skill:write',
        {'slug': 's', 'name': 'n', 'layer': 'business_ops', 'kind': 'composite'},
    ),
    (
        '/api/v1/tools',
        'catalog:tool:write',
        {'slug': 's', 'name': 'n', 'invocation_spec': {}},
    ),
    (
        '/api/v1/datasources',
        'catalog:datasource:write',
        {'slug': 's', 'name': 'n', 'kind': 'database'},
    ),
    (
        '/api/v1/dataproducts',
        'catalog:dataproduct:write',
        {'slug': 's', 'name': 'n', 'contract': {}},
    ),
    ('/api/v1/tenants', 'catalog:tenant:write', {'slug': 's', 'name': 'n'}),
    (
        '/api/v1/principals',
        'catalog:principal:write',
        {
            'tenant_id': ANY_TENANT,
            'kind': 'user',
            'display_name': 'n',
            'external_id': 'x',
        },
    ),
    (
        '/api/v1/environments',
        'catalog:environment:write',
        {
            'tenant_id': ANY_TENANT,
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
        'tenant_id': str(fake_principal.tenant_id),
        'scope': granted,
    }


@pytest.mark.parametrize(('path', 'scope'), READ_ROUTES)
@pytest.mark.asyncio
async def test_read_route_requires_its_own_scope(
    api_client, fake_principal, path, scope
):
    assert (await api_client.get(path)).status_code == 200

    _withhold(api_client, scope, fake_principal)
    resp = await api_client.get(path)
    assert resp.status_code == 403
    assert scope in resp.json()['detail']


@pytest.mark.parametrize(('path', 'scope', 'body'), WRITE_ROUTES)
@pytest.mark.asyncio
async def test_write_route_requires_its_own_scope(
    api_client, fake_principal, path, scope, body
):
    _withhold(api_client, scope, fake_principal)
    resp = await api_client.post(path, json=body)
    assert resp.status_code == 403
    assert scope in resp.json()['detail']
