import json

import httpx
import pytest

from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


def test_derive_realm_admin_base():
    client = KeycloakAdminClient(issuer='https://idp.example/realms/loom', token='t')
    assert client._realm_admin_base == 'https://idp.example/admin/realms/loom'
    assert client._base == 'https://idp.example'


@pytest.mark.asyncio
async def test_login_posts_password_grant_and_returns_ready_client():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/realms/master/protocol/openid-connect/token'
        captured.update(dict(httpx.QueryParams(request.read().decode())))
        return httpx.Response(200, json={'access_token': 'admin-token-xyz'})

    client = await KeycloakAdminClient.login(
        'https://idp.example/realms/loom',
        username='admin',
        password='hunter2',
        transport=httpx.MockTransport(handler),
    )

    assert captured['grant_type'] == 'password'
    assert captured['client_id'] == 'admin-cli'
    assert captured['username'] == 'admin'
    assert captured['password'] == 'hunter2'
    assert client._token == 'admin-token-xyz'
    assert client._issuer == 'https://idp.example/realms/loom'


@pytest.mark.asyncio
async def test_login_honors_admin_realm_and_client_id_overrides():
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        body = dict(httpx.QueryParams(request.read().decode()))
        assert body['client_id'] == 'custom-admin-cli'
        return httpx.Response(200, json={'access_token': 't'})

    await KeycloakAdminClient.login(
        'https://idp.example/realms/loom',
        username='admin',
        password='hunter2',
        admin_realm='internal-admins',
        admin_client_id='custom-admin-cli',
        transport=httpx.MockTransport(handler),
    )

    assert seen_paths == ['/realms/internal-admins/protocol/openid-connect/token']


@pytest.mark.asyncio
async def test_register_client_creates_and_fetches_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/admin/realms/loom/clients' and request.method == 'POST':
            return httpx.Response(
                201,
                headers={
                    'Location': (
                        'https://idp.example/admin/realms/loom/clients/'
                        'internal-uuid-123'
                    )
                },
            )
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/client-secret'
            and request.method == 'GET'
        ):
            return httpx.Response(200, json={'type': 'secret', 'value': 'shh-secret'})
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    result = await client.register_client(
        client_id='loom-catalog-api',
        client_name='Loom Catalog API',
        service_account=True,
    )

    assert result.client_id == 'loom-catalog-api'
    assert result.internal_ref == 'internal-uuid-123'
    assert result.client_secret == 'shh-secret'
    assert result.registration_access_token is None


@pytest.mark.asyncio
async def test_register_client_is_idempotent_on_409():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/admin/realms/loom/clients' and request.method == 'POST':
            return httpx.Response(409, json={'errorMessage': 'Client already exists'})
        if path == '/admin/realms/loom/clients' and request.method == 'GET':
            assert dict(request.url.params) == {'clientId': 'loom-catalog-api'}
            return httpx.Response(200, json=[{'id': 'internal-uuid-123'}])
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/client-secret'
            and request.method == 'GET'
        ):
            return httpx.Response(200, json={'value': 'shh-secret'})
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    result = await client.register_client(
        client_id='loom-catalog-api',
        client_name='Loom Catalog API',
        service_account=True,
    )
    assert result.internal_ref == 'internal-uuid-123'


@pytest.mark.asyncio
async def test_register_client_tolerates_missing_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/admin/realms/loom/clients' and request.method == 'POST':
            return httpx.Response(
                201,
                headers={
                    'Location': (
                        'https://idp.example/admin/realms/loom/clients/'
                        'internal-uuid-123'
                    )
                },
            )
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/client-secret'
            and request.method == 'GET'
        ):
            return httpx.Response(404)
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    result = await client.register_client(
        client_id='loom-catalog-api',
        client_name='Loom Catalog API',
        service_account=False,
    )
    assert result.client_secret is None


@pytest.mark.asyncio
async def test_declare_client_roles_creates_leaf_then_composite_roles():
    created_roles: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
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

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    await client.declare_client_roles('internal-uuid-123', catalog_role_definitions())

    all_scopes = content_scopes() | platform_scopes()
    assert all_scopes <= set(created_roles)
    assert 'catalog-viewer' in created_roles


@pytest.mark.asyncio
async def test_declare_client_roles_is_idempotent_on_409():
    attempted_roles: list[str] = []
    attempted_composites: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/roles'
            and request.method == 'POST'
        ):
            attempted_roles.append(json.loads(request.read())['name'])
            return httpx.Response(409, json={'errorMessage': 'Role already exists'})
        if path.endswith('/composites') and request.method == 'POST':
            attempted_composites.append(path)
            return httpx.Response(409, json={'errorMessage': 'Already associated'})
        if (
            path.startswith('/admin/realms/loom/clients/internal-uuid-123/roles/')
            and request.method == 'GET'
        ):
            role_name = path.rsplit('/', 1)[-1]
            return httpx.Response(
                200, json={'id': f'id-{role_name}', 'name': role_name}
            )
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    await client.declare_client_roles('internal-uuid-123', catalog_role_definitions())

    all_scopes = content_scopes() | platform_scopes()
    assert all_scopes <= set(attempted_roles)
    assert attempted_composites


@pytest.mark.asyncio
async def test_declare_client_roles_still_raises_on_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={'errorMessage': 'boom'})

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.declare_client_roles(
            'internal-uuid-123', catalog_role_definitions()
        )
