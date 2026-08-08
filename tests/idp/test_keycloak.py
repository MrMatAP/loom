import json

import httpx
import pytest

from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


def test_derive_realm_admin_base():
    client = KeycloakAdminClient(issuer='https://idp.example/realms/loom', token='t')
    assert client._realm_admin_base == 'https://idp.example/admin/realms/loom'


@pytest.mark.asyncio
async def test_register_client_and_declare_roles():
    created_roles: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/realms/loom/clients-registrations/openid-connect':
            body = {'client_id': 'loom-catalog-api', 'registration_access_token': 'rat'}
            return httpx.Response(201, json=body)
        if path == '/admin/realms/loom/clients' and request.method == 'GET':
            return httpx.Response(200, json=[{'id': 'internal-uuid-123'}])
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/roles'
            and request.method == 'POST'
        ):
            created_roles.append(json.loads(request.read())['name'])
            return httpx.Response(201)
        if (
            path.startswith('/admin/realms/loom/clients/internal-uuid-123/roles/')
            and request.method == 'GET'
            and not path.endswith('/composites')
        ):
            role_name = path.rsplit('/', 1)[-1]
            return httpx.Response(
                200, json={'id': f'id-{role_name}', 'name': role_name}
            )
        if path.endswith('/composites') and request.method == 'POST':
            return httpx.Response(204)
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    transport = httpx.MockTransport(handler)
    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom', token='t', transport=transport
    )

    result = await client.register_client(
        client_id='loom-catalog-api',
        client_name='Loom Catalog API',
        service_account=True,
    )
    assert result.client_id == 'loom-catalog-api'
    assert result.internal_ref == 'internal-uuid-123'
    assert result.registration_access_token == 'rat'

    await client.declare_client_roles(result.internal_ref, catalog_role_definitions())

    all_scopes = content_scopes() | platform_scopes()
    assert all_scopes <= set(created_roles)
    assert 'catalog-viewer' in created_roles
