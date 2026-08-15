import json
import uuid

import httpx
import pytest

from loom.catalog_client import CatalogApiError, CatalogClient


@pytest.mark.asyncio
async def test_post_sends_bearer_token_and_returns_json_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        captured['authorization'] = request.headers['Authorization']
        captured['body'] = json.loads(request.read())
        return httpx.Response(201, json={'entity_id': 'abc-123', 'slug': 'my-agent'})

    client = CatalogClient(
        'https://api.example.com',
        'access-tok',
        transport=httpx.MockTransport(handler),
    )

    result = await client.post('/api/v1/agents', {'slug': 'my-agent'})

    assert captured['url'] == 'https://api.example.com/api/v1/agents'
    assert captured['authorization'] == 'Bearer access-tok'
    assert captured['body'] == {'slug': 'my-agent'}
    assert result == {'entity_id': 'abc-123', 'slug': 'my-agent'}


def test_tenant_path_builds_the_tenant_nested_url():
    tenant_id = uuid.uuid4()
    client = CatalogClient('https://api.example.com', 'access-tok', tenant_id=tenant_id)

    assert (
        client.tenant_path('/capabilities')
        == f'/api/v1/tenants/{tenant_id}/capabilities'
    )


def test_tenant_path_raises_without_a_configured_tenant_id():
    """Every resource but Tenant itself needs a Tenant to nest under --
    calling `tenant_path()` without one configured is a caller bug, not a
    server rejection, so it fails locally instead of sending a malformed
    URL."""
    client = CatalogClient('https://api.example.com', 'access-tok')

    with pytest.raises(CatalogApiError):
        client.tenant_path('/capabilities')


@pytest.mark.asyncio
async def test_get_reaches_a_tenant_path_built_url():
    tenant_id = uuid.uuid4()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        return httpx.Response(200, json={})

    client = CatalogClient(
        'https://api.example.com',
        'access-tok',
        tenant_id=tenant_id,
        transport=httpx.MockTransport(handler),
    )

    await client.get(client.tenant_path('/capabilities'))

    assert captured['url'] == (
        f'https://api.example.com/api/v1/tenants/{tenant_id}/capabilities'
    )


@pytest.mark.asyncio
async def test_patch_sends_bearer_token_and_returns_json_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['method'] = request.method
        captured['url'] = str(request.url)
        captured['authorization'] = request.headers['Authorization']
        captured['body'] = json.loads(request.read())
        return httpx.Response(200, json={'id': 'abc-123', 'name': 'Renamed'})

    client = CatalogClient(
        'https://api.example.com',
        'access-tok',
        transport=httpx.MockTransport(handler),
    )

    result = await client.patch('/api/v1/tenants/abc-123', {'name': 'Renamed'})

    assert captured['method'] == 'PATCH'
    assert captured['url'] == 'https://api.example.com/api/v1/tenants/abc-123'
    assert captured['authorization'] == 'Bearer access-tok'
    assert captured['body'] == {'name': 'Renamed'}
    assert result == {'id': 'abc-123', 'name': 'Renamed'}


@pytest.mark.asyncio
async def test_patch_raises_catalog_api_error_on_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json={'detail': 'not found'})

    client = CatalogClient(
        'https://api.example.com', 'access-tok', transport=httpx.MockTransport(handler)
    )

    with pytest.raises(CatalogApiError) as exc_info:
        await client.patch('/api/v1/tenants/missing', {'name': 'x'})
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == 'not found'


@pytest.mark.asyncio
async def test_get_sends_bearer_token_and_query_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        captured['authorization'] = request.headers['Authorization']
        return httpx.Response(200, json={'items': [], 'total': 0})

    client = CatalogClient(
        'https://api.example.com',
        'access-tok',
        transport=httpx.MockTransport(handler),
    )

    result = await client.get('/api/v1/agents', params={'limit': 10, 'slug': None})

    assert captured['url'] == 'https://api.example.com/api/v1/agents?limit=10'
    assert captured['authorization'] == 'Bearer access-tok'
    assert result == {'items': [], 'total': 0}


@pytest.mark.asyncio
async def test_get_raises_catalog_api_error_on_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            404, json={'error_code': 'not_found', 'message': 'Agent x not found'}
        )

    client = CatalogClient(
        'https://api.example.com', 'access-tok', transport=httpx.MockTransport(handler)
    )

    with pytest.raises(CatalogApiError) as exc_info:
        await client.get('/api/v1/agents/x')

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == 'Agent x not found'


@pytest.mark.asyncio
async def test_post_strips_trailing_slash_from_base_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == 'https://api.example.com/api/v1/agents'
        return httpx.Response(201, json={})

    client = CatalogClient(
        'https://api.example.com/',
        'access-tok',
        transport=httpx.MockTransport(handler),
    )
    await client.post('/api/v1/agents', {})


@pytest.mark.asyncio
async def test_post_raises_catalog_api_error_with_domain_exception_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            422, json={'error_code': 'validation_error', 'message': 'bad value'}
        )

    client = CatalogClient(
        'https://api.example.com', 'access-tok', transport=httpx.MockTransport(handler)
    )

    with pytest.raises(CatalogApiError) as exc_info:
        await client.post('/api/v1/agents', {})

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == 'bad value'


@pytest.mark.asyncio
async def test_post_raises_catalog_api_error_with_http_exception_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={'detail': 'Invalid token: expired'})

    client = CatalogClient(
        'https://api.example.com', 'access-tok', transport=httpx.MockTransport(handler)
    )

    with pytest.raises(CatalogApiError) as exc_info:
        await client.post('/api/v1/agents', {})

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == 'Invalid token: expired'


@pytest.mark.asyncio
async def test_post_raises_catalog_api_error_with_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, text='internal server error')

    client = CatalogClient(
        'https://api.example.com', 'access-tok', transport=httpx.MockTransport(handler)
    )

    with pytest.raises(CatalogApiError) as exc_info:
        await client.post('/api/v1/agents', {})

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == 'internal server error'
