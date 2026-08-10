import pytest


@pytest.mark.asyncio
async def test_all_routers_are_mounted(api_client):
    for path in (
        '/api/v1/capabilities',
        '/api/v1/agents',
        '/api/v1/skills',
        '/api/v1/tools',
        '/api/v1/datasources',
        '/api/v1/dataproducts',
        '/api/v1/model-endpoints',
        '/api/v1/tenants',
        '/api/v1/principals',
        '/api/v1/environments',
    ):
        params = {'tenant_id': '00000000-0000-0000-0000-000000000000'}
        response = await api_client.get(path, params=params)
        assert response.status_code == 200, f'{path} returned {response.status_code}'


@pytest.mark.asyncio
async def test_missing_token_returns_401(api_client):
    # A request through the raw ASGI app (no dependency override) must 401.
    from httpx import ASGITransport, AsyncClient

    from loom.api.catalog.main import create_app
    from loom.config import RootConfig
    from loom.model.engine import get_async_session_factory

    config = RootConfig(config_path='/dev/null')
    real_app = create_app(config)
    # Populate only what an unauthenticated request path touches: HTTPBearer
    # rejects the missing Authorization header before token_validator is ever
    # used, so running the full lifespan (which eagerly resolves the JWKS
    # URI over the network) is unnecessary and would fail against the
    # default empty issuer.
    real_app.state.session_factory = get_async_session_factory(config.database)
    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get('/api/v1/capabilities')
    assert response.status_code in (401, 403)
