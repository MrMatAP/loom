import httpx
import jwt
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount

from loom.api.catalog.mcp import main as mcp_main
from loom.api.catalog.mcp.main import create_app
from loom.config import RootConfig
from loom.idp.catalog_roles import content_scopes, platform_scopes

ALL_SCOPES = content_scopes() | platform_scopes()


class _FakeTokenValidator:
    """Stands in for `security.TokenValidator` -- see the identical fake in
    `tests/api/catalog/test_mcp.py` for why (skips real JWT/JWKS
    verification)."""

    def __init__(self, claims_by_token: dict[str, dict]) -> None:
        self._claims_by_token = claims_by_token

    def decode(self, token: str) -> dict:
        claims = self._claims_by_token.get(token)
        if claims is None:
            raise jwt.InvalidTokenError('unknown token')
        return claims


def test_create_app_mounts_the_mcp_server_and_exposes_healthz():
    app = create_app(RootConfig(config_path='/dev/null'))

    assert app.title == 'Loom Catalog MCP'
    assert any(isinstance(route, Mount) for route in app.routes)
    paths = {getattr(route, 'path', None) for route in app.routes}
    assert '/healthz' in paths


@pytest.mark.asyncio
async def test_healthz_does_not_require_the_lifespan_to_have_run():
    """`/healthz` doesn't touch McpState, so it must work even before the
    app's lifespan (which resolves the IDP's JWKS over the network) has
    ever run -- the same story as the REST app's own `/healthz`."""
    app = create_app(RootConfig(config_path='/dev/null'))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


@pytest.mark.asyncio
async def test_mcp_tool_call_over_real_http_reads_the_authorization_header(
    monkeypatch, async_session_factory, fake_principal
):
    """The other MCP tests (`tests/api/catalog/test_mcp.py`) monkeypatch
    `get_http_headers` directly, which proves the tool logic but never
    exercises the actual plumbing it depends on in production: a real HTTP
    request through the `/mcp` mount, Starlette's request-context
    middleware, and fastmcp's own header extraction off it. This one
    drives a real Streamable HTTP call through `create_app`'s mount (via
    `ASGITransport`, so still no real socket) and confirms the
    Authorization header genuinely round-trips end to end -- and that its
    absence is genuinely what triggers "Missing bearer token", not an
    accident of the mocked path."""
    token_validator = _FakeTokenValidator(
        {
            'valid-all-scopes': {
                'sub': 'test-user',
                'tenant_id': str(fake_principal.tenant_id),
                'scope': ' '.join(sorted(ALL_SCOPES)),
            },
        }
    )
    monkeypatch.setattr(mcp_main, 'TokenValidator', lambda config: token_validator)
    monkeypatch.setattr(
        mcp_main, 'get_async_session_factory', lambda config: async_session_factory
    )

    app = create_app(RootConfig(config_path='/dev/null'))

    def _client_factory(*, headers, auth, follow_redirects, **kwargs):
        return httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url='http://test',
            headers=headers,
            auth=auth,
            follow_redirects=follow_redirects,
            **kwargs,
        )

    async with app.router.lifespan_context(app):
        authed_transport = StreamableHttpTransport(
            'http://test/mcp',
            headers={'Authorization': 'Bearer valid-all-scopes'},
            httpx_client_factory=_client_factory,
        )
        async with Client(authed_transport) as client:
            result = await client.call_tool(
                'create_capability', {'data': {'slug': 'e2e-cap', 'name': 'E2E Cap'}}
            )
        assert result.data.slug == 'e2e-cap'

        unauthed_transport = StreamableHttpTransport(
            'http://test/mcp', httpx_client_factory=_client_factory
        )
        async with Client(unauthed_transport) as client:
            with pytest.raises(ToolError, match='Missing bearer token'):
                await client.call_tool(
                    'create_capability', {'data': {'slug': 'e2e-cap-2', 'name': 'x'}}
                )
