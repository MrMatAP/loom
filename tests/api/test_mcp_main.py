import uuid

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
from loom.model.enums import PrincipalKind
from loom.model.tenant import Principal, Tenant

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


def test_mcp_mount_runs_stateless_so_any_replica_can_serve_any_request():
    """No MCP session may be pinned to whichever pod happened to handle its
    `initialize` call -- a later request for that session landing on a
    *different* replica behind a Service/LoadBalancer must still work.
    fastmcp's own observable signal for `stateless_http=True` is that the
    `/mcp` route drops GET (no SSE stream, since there's no session to push
    notifications into) and accepts only POST/DELETE -- assert that,
    rather than the `stateless_http=True` kwarg itself, so this fails if a
    fastmcp upgrade ever changes what the flag does instead of just its
    name."""
    app = create_app(RootConfig(config_path='/dev/null'))
    mount = next(route for route in app.routes if isinstance(route, Mount))
    mcp_route = next(r for r in mount.app.routes if getattr(r, 'path', None) == '/mcp')

    assert mcp_route.methods == {'POST', 'DELETE'}


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
async def test_lifespan_builds_the_token_validator_against_the_mcp_audience(
    monkeypatch, async_session_factory
):
    """The load-bearing claim of collapsing `idp register` into four
    clients: the MCP server must validate tokens against `auth.mcp_audience`,
    not `auth.audience` -- otherwise it's silently still sharing the REST
    API's resource server and registering it separately
    (`idp.idp_register`'s MCP step) buys nothing. `TokenValidator` itself is
    still faked out (real JWKS resolution needs network), but the fake
    captures the `AuthConfig` it was built with instead of discarding it,
    so this fails if `mcp.main.create_app`'s lifespan ever goes back to
    sharing `config.auth` wholesale with the REST API."""
    captured: dict[str, object] = {}

    def _capture_and_build(config):
        captured['config'] = config
        return _FakeTokenValidator({})

    monkeypatch.setattr(mcp_main, 'TokenValidator', _capture_and_build)
    monkeypatch.setattr(
        mcp_main, 'get_async_session_factory', lambda config: async_session_factory
    )

    config = RootConfig(config_path='/dev/null')
    config.auth.audience = 'loom-catalog-api'
    config.auth.mcp_audience = 'loom-catalog-api-mcp'
    app = create_app(config)

    async with app.router.lifespan_context(app):
        pass

    assert captured['config'].audience == 'loom-catalog-api-mcp'
    assert captured['config'].audience != config.auth.audience


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
                'scope': ' '.join(sorted(ALL_SCOPES)),
            },
        }
    )
    monkeypatch.setattr(mcp_main, 'TokenValidator', lambda config: token_validator)
    monkeypatch.setattr(
        mcp_main, 'get_async_session_factory', lambda config: async_session_factory
    )

    app = create_app(RootConfig(config_path='/dev/null'))
    tenant_id = str(fake_principal.tenant_id)

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
                'create_capability',
                {'tenant_id': tenant_id, 'data': {'name': 'E2E Cap'}},
            )
        assert result.data.name == 'E2E Cap'

        unauthed_transport = StreamableHttpTransport(
            'http://test/mcp', httpx_client_factory=_client_factory
        )
        async with Client(unauthed_transport) as client:
            with pytest.raises(ToolError, match='Missing bearer token'):
                await client.call_tool(
                    'create_capability',
                    {'tenant_id': tenant_id, 'data': {'name': 'x'}},
                )


@pytest.mark.asyncio
async def test_mcp_tool_call_over_real_http_targets_the_given_tenant(
    monkeypatch, async_session_factory
):
    """`tenant_id` is a plain tool argument now (see `mcp/server.py`'s
    `_register_resource_tools`), not a header fastmcp has to be trusted to
    forward -- provision the same `sub` into two Tenants (mirrors
    `tests/api/test_tenant_isolation.py`'s `multi_tenant_identity`) and
    confirm a real Streamable HTTP call through the `/mcp` mount only ever
    resolves to the Tenant named in the call, both directions."""
    async with async_session_factory() as session:
        first_tenant = Tenant(slug='mcp-hint-first', name='MCP Hint First')
        second_tenant = Tenant(slug='mcp-hint-second', name='MCP Hint Second')
        session.add_all([first_tenant, second_tenant])
        await session.flush()
        session.add_all(
            [
                Principal(
                    tenant_id=first_tenant.id,
                    kind=PrincipalKind.USER,
                    external_id='mcp-hint-user',
                ),
                Principal(
                    tenant_id=second_tenant.id,
                    kind=PrincipalKind.USER,
                    external_id='mcp-hint-user',
                ),
            ]
        )
        await session.commit()
        first_tenant_id, second_tenant_id = first_tenant.id, second_tenant.id

    token_validator = _FakeTokenValidator(
        {
            'valid-all-scopes': {
                'sub': 'mcp-hint-user',
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
        transport = StreamableHttpTransport(
            'http://test/mcp',
            headers={'Authorization': 'Bearer valid-all-scopes'},
            httpx_client_factory=_client_factory,
        )
        async with Client(transport) as client:
            result = await client.call_tool(
                'create_capability',
                {'tenant_id': str(first_tenant_id), 'data': {'name': 'MCP Hint Cap'}},
            )
            assert result.data.name == 'MCP Hint Cap'

            with pytest.raises(ToolError, match='No principal'):
                await client.call_tool(
                    'get_capability',
                    {
                        'tenant_id': str(uuid.uuid4()),
                        'entity_id': str(result.data.entity_id),
                    },
                )

            with pytest.raises(ToolError, match='not found'):
                await client.call_tool(
                    'get_capability',
                    {
                        'tenant_id': str(second_tenant_id),
                        'entity_id': str(result.data.entity_id),
                    },
                )
