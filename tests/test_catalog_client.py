import json

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
