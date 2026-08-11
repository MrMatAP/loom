import jwt
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from loom.api.catalog.mcp.server import McpState, create_mcp_server
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.model.capability import Capability

ALL_SCOPES = content_scopes() | platform_scopes()


class _FakeTokenValidator:
    """Stands in for `security.TokenValidator`: maps a bearer token string
    straight to a claims dict, skipping real JWT signature/JWKS
    verification -- exactly what the fixture-seeded principal below needs
    and nothing the real validator's network calls would provide in a unit
    test."""

    def __init__(self, claims_by_token: dict[str, dict]) -> None:
        self._claims_by_token = claims_by_token

    def decode(self, token: str) -> dict:
        claims = self._claims_by_token.get(token)
        if claims is None:
            raise jwt.InvalidTokenError('unknown token')
        return claims


@pytest.fixture
def mcp_state(async_session_factory, fake_principal):
    state = McpState()
    state.session_factory = async_session_factory
    state.token_validator = _FakeTokenValidator(
        {
            'valid-all-scopes': {
                'sub': 'test-user',
                'tenant_id': str(fake_principal.tenant_id),
                'scope': ' '.join(sorted(ALL_SCOPES)),
            },
            'valid-read-only': {
                'sub': 'test-user',
                'tenant_id': str(fake_principal.tenant_id),
                'scope': 'catalog:capability:read',
            },
        }
    )
    return state


@pytest.fixture
def mcp_server(mcp_state):
    return create_mcp_server(mcp_state)


def _auth_headers(monkeypatch, token: str | None):
    """Stand in for the incoming HTTP request's Authorization header --
    `mcp/server.py` reads it via `get_http_headers`, fastmcp's own context
    accessor, which the in-memory transport used below never populates."""
    headers = {'authorization': f'Bearer {token}'} if token else {}
    monkeypatch.setattr(
        'loom.api.catalog.mcp.server.get_http_headers', lambda **kwargs: headers
    )


@pytest.mark.asyncio
async def test_server_registers_the_three_create_tools(mcp_server):
    async with Client(mcp_server) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == {
        'create_capability',
        'create_model',
        'create_agent',
    }


@pytest.mark.asyncio
async def test_create_capability_tool_creates_via_the_service(
    mcp_server, monkeypatch, async_session_factory
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            'create_capability',
            {'data': {'slug': 'latency-slo', 'name': 'Latency SLO'}},
        )

    assert result.data.slug == 'latency-slo'
    assert result.data.lifecycle_state == 'draft'

    async with async_session_factory() as session:
        row = await session.scalar(
            select(Capability).where(Capability.slug == 'latency-slo')
        )
        assert row is not None


@pytest.mark.asyncio
async def test_create_model_tool_creates_via_the_service(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            'create_model',
            {
                'data': {
                    'slug': 'claude-opus',
                    'name': 'Claude Opus',
                    'protocol': 'anthropic_messages',
                    'model': 'claude-opus-4',
                }
            },
        )

    assert result.data.slug == 'claude-opus'
    assert result.data.protocol == 'anthropic_messages'


@pytest.mark.asyncio
async def test_create_agent_tool_creates_via_the_service(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            'create_agent',
            {
                'data': {
                    'slug': 'triage-bot',
                    'name': 'Triage Bot',
                    'layer': 'business_tech',
                    'llm_config': {},
                    'prompt': 'You triage tickets.',
                    'memory_scope': 'session',
                }
            },
        )

    assert result.data.slug == 'triage-bot'
    assert result.data.layer == 'business_tech'


@pytest.mark.asyncio
async def test_tool_call_without_bearer_token_is_rejected(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, None)

    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='Missing bearer token'):
            await client.call_tool(
                'create_capability', {'data': {'slug': 'x', 'name': 'X'}}
            )


@pytest.mark.asyncio
async def test_tool_call_with_invalid_token_is_rejected(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, 'not-a-real-token')

    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='Invalid token'):
            await client.call_tool(
                'create_capability', {'data': {'slug': 'x', 'name': 'X'}}
            )


@pytest.mark.asyncio
async def test_tool_call_without_required_scope_is_rejected(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, 'valid-read-only')

    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='Missing required scope'):
            await client.call_tool(
                'create_capability', {'data': {'slug': 'x', 'name': 'X'}}
            )


@pytest.mark.asyncio
async def test_tool_call_before_lifespan_starts_is_a_clear_error(monkeypatch):
    """An uninitialized McpState (the FastAPI lifespan never ran, e.g. the
    ASGI app was mounted but never started) fails loudly rather than with
    an opaque AttributeError deep in a tool body."""
    _auth_headers(monkeypatch, 'valid-all-scopes')
    state = McpState()
    mcp = create_mcp_server(state)

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match='not initialized'):
            await client.call_tool(
                'create_capability', {'data': {'slug': 'x', 'name': 'X'}}
            )
