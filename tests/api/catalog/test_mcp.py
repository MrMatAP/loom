import jwt
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from loom.api.catalog.mcp.server import McpState, create_mcp_server
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.model.capability import Capability

ALL_SCOPES = content_scopes() | platform_scopes()


def _expected_tool_names(label: str, plural: str) -> set[str]:
    return {
        f'create_{label}',
        f'get_{label}',
        f'list_{plural}',
        f'list_{label}_versions',
        f'get_{label}_version',
        f'update_{label}',
        f'transition_{label}',
    }


ALL_TOOL_NAMES = (
    _expected_tool_names('capability', 'capabilities')
    | _expected_tool_names('model', 'models')
    | _expected_tool_names('agent', 'agents')
)


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
async def test_server_registers_all_twenty_one_tools(mcp_server):
    async with Client(mcp_server) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == ALL_TOOL_NAMES
    assert len(ALL_TOOL_NAMES) == 21


@pytest.mark.parametrize(
    ('label', 'plural'),
    [('capability', 'capabilities'), ('model', 'models'), ('agent', 'agents')],
)
def test_each_resource_registers_its_seven_tools(label, plural):
    assert _expected_tool_names(label, plural) <= ALL_TOOL_NAMES


# --- Capability: full verb coverage (the exemplar resource) ----------------


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
async def test_capability_lifecycle_get_list_versions_update_transition(
    mcp_server, monkeypatch
):
    """One end-to-end run through every capability verb, each building on
    the last -- a stronger check than isolated per-verb calls that a
    create's entity_id/version are exactly what get/update/transition
    expect to be given."""
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_capability',
            {'data': {'slug': 'availability-slo', 'name': 'Availability SLO'}},
        )
        entity_id = str(created.data.entity_id)

        got = await client.call_tool('get_capability', {'entity_id': entity_id})
        assert got.data.slug == 'availability-slo'
        assert got.data.version == 1

        listed = await client.call_tool(
            'list_capabilities', {'slug': 'availability-slo'}
        )
        assert listed.data.total == 1
        assert listed.data.items[0].entity_id == created.data.entity_id

        updated = await client.call_tool(
            'update_capability',
            {
                'entity_id': entity_id,
                'data': {
                    'slug': 'availability-slo',
                    'name': 'Availability SLO v2',
                    'target_metrics': [{'name': 'uptime'}],
                },
            },
        )
        assert updated.data.version == 2
        assert updated.data.name == 'Availability SLO v2'

        versions = await client.call_tool(
            'list_capability_versions', {'entity_id': entity_id}
        )
        assert [v.version for v in versions.data] == [1, 2]

        version_1 = await client.call_tool(
            'get_capability_version', {'entity_id': entity_id, 'version': 1}
        )
        assert version_1.data.name == 'Availability SLO'

        transitioned = await client.call_tool(
            'transition_capability',
            {'entity_id': entity_id, 'version': 2, 'to_state': 'in_review'},
        )
        assert transitioned.data.lifecycle_state == 'in_review'


@pytest.mark.asyncio
async def test_get_capability_tool_raises_on_unknown_entity(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, 'valid-all-scopes')
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='not found'):
            await client.call_tool(
                'get_capability',
                {'entity_id': '00000000-0000-0000-0000-000000000000'},
            )


@pytest.mark.asyncio
async def test_transition_capability_tool_raises_on_illegal_transition(
    mcp_server, monkeypatch
):
    _auth_headers(monkeypatch, 'valid-all-scopes')
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_capability', {'data': {'slug': 'x-cap', 'name': 'X'}}
        )
        with pytest.raises(ToolError, match='not a legal transition'):
            await client.call_tool(
                'transition_capability',
                {
                    'entity_id': str(created.data.entity_id),
                    'version': 1,
                    'to_state': 'retired',
                },
            )


@pytest.mark.asyncio
async def test_read_only_token_cannot_update_or_transition_capability(
    mcp_server, monkeypatch
):
    """`valid-read-only` only carries `catalog:capability:read` -- update
    and transition need `:write`/`:transition` respectively, distinct
    scopes read access must not imply."""
    _auth_headers(monkeypatch, 'valid-all-scopes')
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_capability', {'data': {'slug': 'ro-cap', 'name': 'RO'}}
        )
    entity_id = str(created.data.entity_id)

    _auth_headers(monkeypatch, 'valid-read-only')
    async with Client(mcp_server) as client:
        got = await client.call_tool('get_capability', {'entity_id': entity_id})
        assert got.data.slug == 'ro-cap'

        with pytest.raises(ToolError, match='Missing required scope'):
            await client.call_tool(
                'update_capability',
                {
                    'entity_id': entity_id,
                    'data': {'slug': 'ro-cap', 'name': 'RO'},
                },
            )
        with pytest.raises(ToolError, match='Missing required scope'):
            await client.call_tool(
                'transition_capability',
                {'entity_id': entity_id, 'version': 1, 'to_state': 'in_review'},
            )


# --- Model / Agent: smoke coverage (create + get) ---------------------------


@pytest.mark.asyncio
async def test_create_model_tool_creates_via_the_service(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
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
        assert created.data.slug == 'claude-opus'
        assert created.data.protocol == 'anthropic_messages'

        got = await client.call_tool(
            'get_model', {'entity_id': str(created.data.entity_id)}
        )
        assert got.data.model == 'claude-opus-4'


@pytest.mark.asyncio
async def test_create_agent_tool_creates_via_the_service(mcp_server, monkeypatch):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
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
        assert created.data.slug == 'triage-bot'
        assert created.data.layer == 'business_tech'

        got = await client.call_tool(
            'get_agent', {'entity_id': str(created.data.entity_id)}
        )
        assert got.data.prompt == 'You triage tickets.'


# --- Cross-cutting auth/state checks (verb-agnostic) ------------------------


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
