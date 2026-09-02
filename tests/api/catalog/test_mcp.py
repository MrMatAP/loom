import jwt
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from loom.api.catalog.mcp.server import McpState, create_mcp_server
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.persistence.capability import Capability

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


_SUB_RESOURCE_TOOL_NAMES = {
    'add_skill_node',
    'list_skill_nodes',
    'add_skill_edge',
    'list_skill_edges',
    'add_tool_data_binding',
    'list_tool_data_bindings',
    'add_dataproduct_lineage',
    'list_dataproduct_lineage',
}

_ENVIRONMENT_TOOL_NAMES = {
    'create_environment',
    'get_environment',
    'list_environments',
    'update_environment',
}

ALL_TOOL_NAMES = (
    _expected_tool_names('capability', 'capabilities')
    | _expected_tool_names('model', 'models')
    | _expected_tool_names('agent', 'agents')
    | _expected_tool_names('skill', 'skills')
    | _expected_tool_names('tool', 'tools')
    | _expected_tool_names('datasource', 'datasources')
    | _expected_tool_names('dataproduct', 'dataproducts')
    | _SUB_RESOURCE_TOOL_NAMES
    | _ENVIRONMENT_TOOL_NAMES
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
                'scope': ' '.join(sorted(ALL_SCOPES)),
            },
            'valid-read-only': {
                'sub': 'test-user',
                'scope': 'catalog:capability:read',
            },
            'valid-all-scopes-no-principal': {
                'sub': 'ghost-user-with-no-principal-row',
                'scope': ' '.join(sorted(ALL_SCOPES)),
            },
        }
    )
    return state


@pytest.fixture
def mcp_server(mcp_state):
    return create_mcp_server(mcp_state)


@pytest.fixture
def tenant_id(fake_principal) -> str:
    return str(fake_principal.tenant_id)


def _auth_headers(monkeypatch, token: str | None):
    """Stand in for the incoming HTTP request's Authorization header --
    `mcp/server.py` reads it via `get_http_headers`, fastmcp's own context
    accessor, which the in-memory transport used below never populates."""
    headers = {'authorization': f'Bearer {token}'} if token else {}
    monkeypatch.setattr(
        'loom.api.catalog.mcp.server.get_http_headers', lambda **kwargs: headers
    )


@pytest.mark.asyncio
async def test_server_registers_all_sixty_one_tools(mcp_server):
    async with Client(mcp_server) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == ALL_TOOL_NAMES
    assert len(ALL_TOOL_NAMES) == 61


@pytest.mark.parametrize(
    ('label', 'plural'),
    [
        ('capability', 'capabilities'),
        ('model', 'models'),
        ('agent', 'agents'),
        ('skill', 'skills'),
        ('tool', 'tools'),
        ('datasource', 'datasources'),
        ('dataproduct', 'dataproducts'),
    ],
)
def test_each_resource_registers_its_seven_tools(label, plural):
    assert _expected_tool_names(label, plural) <= ALL_TOOL_NAMES


# --- Capability: full verb coverage (the exemplar resource) ----------------


@pytest.mark.asyncio
async def test_create_capability_tool_creates_via_the_service(
    mcp_server, monkeypatch, async_session_factory, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            'create_capability',
            {'tenant_id': tenant_id, 'data': {'name': 'Latency SLO'}},
        )

    assert result.data.name == 'Latency SLO'
    assert result.data.lifecycle_state == 'draft'

    async with async_session_factory() as session:
        row = await session.scalar(
            select(Capability).where(Capability.name == 'Latency SLO')
        )
        assert row is not None


@pytest.mark.asyncio
async def test_capability_lifecycle_get_list_versions_update_transition(
    mcp_server, monkeypatch, tenant_id
):
    """One end-to-end run through every capability verb, each building on
    the last -- a stronger check than isolated per-verb calls that a
    create's entity_id/version are exactly what get/update/transition
    expect to be given."""
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_capability',
            {'tenant_id': tenant_id, 'data': {'name': 'Availability SLO'}},
        )
        entity_id = str(created.data.entity_id)

        got = await client.call_tool(
            'get_capability', {'tenant_id': tenant_id, 'entity_id': entity_id}
        )
        assert got.data.name == 'Availability SLO'
        assert got.data.version == 1

        listed = await client.call_tool('list_capabilities', {'tenant_id': tenant_id})
        assert listed.data.total == 1
        assert listed.data.items[0].entity_id == created.data.entity_id

        updated = await client.call_tool(
            'update_capability',
            {
                'tenant_id': tenant_id,
                'entity_id': entity_id,
                'data': {
                    'name': 'Availability SLO v2',
                    'target_metrics': [{'name': 'uptime'}],
                },
            },
        )
        assert updated.data.version == 2
        assert updated.data.name == 'Availability SLO v2'

        versions = await client.call_tool(
            'list_capability_versions', {'tenant_id': tenant_id, 'entity_id': entity_id}
        )
        assert [v.version for v in versions.data] == [1, 2]

        version_1 = await client.call_tool(
            'get_capability_version',
            {'tenant_id': tenant_id, 'entity_id': entity_id, 'version': 1},
        )
        assert version_1.data.name == 'Availability SLO'

        transitioned = await client.call_tool(
            'transition_capability',
            {
                'tenant_id': tenant_id,
                'entity_id': entity_id,
                'version': 2,
                'to_state': 'in_review',
            },
        )
        assert transitioned.data.lifecycle_state == 'in_review'


@pytest.mark.asyncio
async def test_get_capability_tool_raises_on_unknown_entity(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='not found'):
            await client.call_tool(
                'get_capability',
                {
                    'tenant_id': tenant_id,
                    'entity_id': '00000000-0000-0000-0000-000000000000',
                },
            )


@pytest.mark.asyncio
async def test_transition_capability_tool_raises_on_illegal_transition(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_capability', {'tenant_id': tenant_id, 'data': {'name': 'X'}}
        )
        with pytest.raises(ToolError, match='not a legal transition'):
            await client.call_tool(
                'transition_capability',
                {
                    'tenant_id': tenant_id,
                    'entity_id': str(created.data.entity_id),
                    'version': 1,
                    'to_state': 'retired',
                },
            )


@pytest.mark.asyncio
async def test_read_only_token_cannot_update_or_transition_capability(
    mcp_server, monkeypatch, tenant_id
):
    """`valid-read-only` only carries `catalog:capability:read` -- update
    and transition need `:write`/`:transition` respectively, distinct
    scopes read access must not imply."""
    _auth_headers(monkeypatch, 'valid-all-scopes')
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_capability', {'tenant_id': tenant_id, 'data': {'name': 'RO'}}
        )
    entity_id = str(created.data.entity_id)

    _auth_headers(monkeypatch, 'valid-read-only')
    async with Client(mcp_server) as client:
        got = await client.call_tool(
            'get_capability', {'tenant_id': tenant_id, 'entity_id': entity_id}
        )
        assert got.data.name == 'RO'

        with pytest.raises(ToolError, match='Missing required scope'):
            await client.call_tool(
                'update_capability',
                {
                    'tenant_id': tenant_id,
                    'entity_id': entity_id,
                    'data': {'name': 'RO'},
                },
            )
        with pytest.raises(ToolError, match='Missing required scope'):
            await client.call_tool(
                'transition_capability',
                {
                    'tenant_id': tenant_id,
                    'entity_id': entity_id,
                    'version': 1,
                    'to_state': 'in_review',
                },
            )


# --- Model / Agent: smoke coverage (create + get) ---------------------------


@pytest.mark.asyncio
async def test_create_model_tool_creates_via_the_service(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_model',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'Claude Opus',
                    'protocol': 'anthropic_messages',
                    'model': 'claude-opus-4',
                },
            },
        )
        assert created.data.name == 'Claude Opus'
        assert created.data.protocol == 'anthropic_messages'

        got = await client.call_tool(
            'get_model',
            {'tenant_id': tenant_id, 'entity_id': str(created.data.entity_id)},
        )
        assert got.data.model == 'claude-opus-4'


@pytest.mark.asyncio
async def test_create_agent_tool_creates_via_the_service(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_agent',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'Triage Bot',
                    'layer': 'business_tech',
                    'llm_config': {},
                    'prompt': 'You triage tickets.',
                    'memory_scope': 'session',
                },
            },
        )
        assert created.data.name == 'Triage Bot'
        assert created.data.layer == 'business_tech'

        got = await client.call_tool(
            'get_agent',
            {'tenant_id': tenant_id, 'entity_id': str(created.data.entity_id)},
        )
        assert got.data.prompt == 'You triage tickets.'


# --- Skill/Tool/DataSource/DataProduct: smoke coverage (create + get) ------


@pytest.mark.asyncio
async def test_create_skill_tool_creates_via_the_service(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_skill',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'Triage Workflow',
                    'layer': 'business_ops',
                    'kind': 'composite',
                    'is_entry_point': True,
                },
            },
        )
        assert created.data.name == 'Triage Workflow'
        assert created.data.is_entry_point is True

        got = await client.call_tool(
            'get_skill',
            {'tenant_id': tenant_id, 'entity_id': str(created.data.entity_id)},
        )
        assert got.data.kind == 'composite'


@pytest.mark.asyncio
async def test_create_tool_tool_creates_via_the_service(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_tool',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'Ticket Lookup',
                    'invocation_spec': {'method': 'GET', 'path': '/tickets/{id}'},
                },
            },
        )
        assert created.data.name == 'Ticket Lookup'

        got = await client.call_tool(
            'get_tool',
            {'tenant_id': tenant_id, 'entity_id': str(created.data.entity_id)},
        )
        assert got.data.invocation_spec == {'method': 'GET', 'path': '/tickets/{id}'}


@pytest.mark.asyncio
async def test_create_datasource_tool_creates_via_the_service(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_datasource',
            {
                'tenant_id': tenant_id,
                'data': {'name': 'Tickets DB', 'kind': 'database'},
            },
        )
        assert created.data.name == 'Tickets DB'

        got = await client.call_tool(
            'get_datasource',
            {'tenant_id': tenant_id, 'entity_id': str(created.data.entity_id)},
        )
        assert got.data.kind == 'database'


@pytest.mark.asyncio
async def test_create_dataproduct_tool_creates_via_the_service(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_dataproduct',
            {
                'tenant_id': tenant_id,
                'data': {'name': 'Ticket Summary', 'contract': {'schema': 'v1'}},
            },
        )
        assert created.data.name == 'Ticket Summary'

        got = await client.call_tool(
            'get_dataproduct',
            {'tenant_id': tenant_id, 'entity_id': str(created.data.entity_id)},
        )
        assert got.data.contract == {'schema': 'v1'}


# --- Sub-resource tools: Skill graph, Tool data bindings, DataProduct
# lineage -- keyed on (entity_id, version), so they don't fit
# `_register_resource_tools`'s shape. -----------------------------------


@pytest.mark.asyncio
async def test_skill_node_and_edge_tools(mcp_server, monkeypatch, tenant_id):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        skill = await client.call_tool(
            'create_skill',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'Graph Skill',
                    'layer': 'infra_ops',
                    'kind': 'composite',
                },
            },
        )
        entity_id = str(skill.data.entity_id)

        # node_a references an Agent, not a Tool: Tool is never a legal
        # edge *source* (loom.domain.skill.add_edge -- it's the
        # unconditionally-legal call *target* every layer may reach,
        # never the caller), so two Tool-referencing nodes can't be wired
        # to each other.
        agent_a = await client.call_tool(
            'create_agent',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'A',
                    'layer': 'infra_ops',
                    'llm_config': {},
                    'prompt': 'p',
                    'memory_scope': 'none',
                },
            },
        )
        tool_b = await client.call_tool(
            'create_tool',
            {'tenant_id': tenant_id, 'data': {'name': 'B', 'invocation_spec': {}}},
        )

        node_a = await client.call_tool(
            'add_skill_node',
            {
                'tenant_id': tenant_id,
                'entity_id': entity_id,
                'version': 1,
                'data': {
                    'node_key': 'a',
                    'node_type': 'agent',
                    'agent_id': str(agent_a.data.id),
                },
            },
        )
        node_b = await client.call_tool(
            'add_skill_node',
            {
                'tenant_id': tenant_id,
                'entity_id': entity_id,
                'version': 1,
                'data': {
                    'node_key': 'b',
                    'node_type': 'tool',
                    'tool_id': str(tool_b.data.id),
                },
            },
        )

        nodes = await client.call_tool(
            'list_skill_nodes',
            {'tenant_id': tenant_id, 'entity_id': entity_id, 'version': 1},
        )
        assert {n.node_key for n in nodes.data} == {'a', 'b'}

        edge = await client.call_tool(
            'add_skill_edge',
            {
                'tenant_id': tenant_id,
                'entity_id': entity_id,
                'version': 1,
                'data': {
                    'from_node_id': str(node_a.data.id),
                    'to_node_id': str(node_b.data.id),
                },
            },
        )
        assert edge.data.from_node_id == node_a.data.id

        edges = await client.call_tool(
            'list_skill_edges',
            {'tenant_id': tenant_id, 'entity_id': entity_id, 'version': 1},
        )
        assert len(edges.data) == 1


@pytest.mark.asyncio
async def test_tool_data_binding_tools(mcp_server, monkeypatch, tenant_id):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        datasource = await client.call_tool(
            'create_datasource',
            {'tenant_id': tenant_id, 'data': {'name': 'DS', 'kind': 'database'}},
        )
        tool = await client.call_tool(
            'create_tool',
            {
                'tenant_id': tenant_id,
                'data': {'name': 'T', 'invocation_spec': {}},
            },
        )
        entity_id = str(tool.data.entity_id)

        binding = await client.call_tool(
            'add_tool_data_binding',
            {
                'tenant_id': tenant_id,
                'entity_id': entity_id,
                'version': 1,
                'data': {
                    'datasource_id': str(datasource.data.id),
                    'access_mode': 'read',
                },
            },
        )
        assert binding.data.access_mode == 'read'

        bindings = await client.call_tool(
            'list_tool_data_bindings',
            {'tenant_id': tenant_id, 'entity_id': entity_id, 'version': 1},
        )
        assert len(bindings.data) == 1


@pytest.mark.asyncio
async def test_dataproduct_lineage_tools(mcp_server, monkeypatch, tenant_id):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        datasource = await client.call_tool(
            'create_datasource',
            {'tenant_id': tenant_id, 'data': {'name': 'DS', 'kind': 'database'}},
        )
        dataproduct = await client.call_tool(
            'create_dataproduct',
            {'tenant_id': tenant_id, 'data': {'name': 'DP', 'contract': {}}},
        )
        entity_id = str(dataproduct.data.entity_id)

        lineage = await client.call_tool(
            'add_dataproduct_lineage',
            {
                'tenant_id': tenant_id,
                'entity_id': entity_id,
                'version': 1,
                'data': {'source_datasource_id': str(datasource.data.id)},
            },
        )
        assert lineage.data.source_datasource_id == datasource.data.id

        edges = await client.call_tool(
            'list_dataproduct_lineage',
            {'tenant_id': tenant_id, 'entity_id': entity_id, 'version': 1},
        )
        assert len(edges.data) == 1


# --- Environment tools: not a VersionedEntity, scope-only auth (no
# Principal resolution -- mirrors `environment/router.py` exactly). ---------


@pytest.mark.asyncio
async def test_environment_create_get_list_update(mcp_server, monkeypatch, tenant_id):
    _auth_headers(monkeypatch, 'valid-all-scopes')

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_environment',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'sandbox-1',
                    'kind': 'sandbox',
                    'compute_boundary_ref': 'cluster-a',
                    'network_boundary_ref': 'vpc-a',
                },
            },
        )
        assert created.data.name == 'sandbox-1'
        environment_id = str(created.data.id)

        got = await client.call_tool(
            'get_environment',
            {'tenant_id': tenant_id, 'environment_id': environment_id},
        )
        assert got.data.compute_boundary_ref == 'cluster-a'

        listed = await client.call_tool('list_environments', {'tenant_id': tenant_id})
        assert listed.data.total == 1

        updated = await client.call_tool(
            'update_environment',
            {
                'tenant_id': tenant_id,
                'environment_id': environment_id,
                'data': {'compute_boundary_ref': 'cluster-b'},
            },
        )
        assert updated.data.compute_boundary_ref == 'cluster-b'
        assert updated.data.network_boundary_ref == 'vpc-a'


@pytest.mark.asyncio
async def test_environment_tool_does_not_require_a_principal_row(
    mcp_server, monkeypatch, tenant_id
):
    """The REST route never calls `get_current_principal` -- only
    `require_scopes` -- so a token with the right scope but no Principal
    row in `tenant_id` still succeeds. `fake_principal`'s own identity is
    used for every other test in this file; this one deliberately uses a
    `sub` with no Principal row at all."""
    monkeypatch.setattr(
        'loom.api.catalog.mcp.server.get_http_headers',
        lambda **kwargs: {'authorization': 'Bearer valid-all-scopes-no-principal'},
    )
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            'create_environment',
            {
                'tenant_id': tenant_id,
                'data': {
                    'name': 'no-principal-env',
                    'kind': 'sandbox',
                    'compute_boundary_ref': 'cluster-x',
                    'network_boundary_ref': 'vpc-x',
                },
            },
        )
        assert created.data.name == 'no-principal-env'


# --- Cross-cutting auth/state checks (verb-agnostic) ------------------------


@pytest.mark.asyncio
async def test_tool_call_without_bearer_token_is_rejected(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, None)

    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='Missing bearer token'):
            await client.call_tool(
                'create_capability', {'tenant_id': tenant_id, 'data': {'name': 'X'}}
            )


@pytest.mark.asyncio
async def test_tool_call_with_invalid_token_is_rejected(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'not-a-real-token')

    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='Invalid token'):
            await client.call_tool(
                'create_capability', {'tenant_id': tenant_id, 'data': {'name': 'X'}}
            )


@pytest.mark.asyncio
async def test_tool_call_without_required_scope_is_rejected(
    mcp_server, monkeypatch, tenant_id
):
    _auth_headers(monkeypatch, 'valid-read-only')

    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match='Missing required scope'):
            await client.call_tool(
                'create_capability', {'tenant_id': tenant_id, 'data': {'name': 'X'}}
            )


@pytest.mark.asyncio
async def test_tool_call_before_lifespan_starts_is_a_clear_error(
    monkeypatch, tenant_id
):
    """An uninitialized McpState (the FastAPI lifespan never ran, e.g. the
    ASGI app was mounted but never started) fails loudly rather than with
    an opaque AttributeError deep in a tool body."""
    _auth_headers(monkeypatch, 'valid-all-scopes')
    state = McpState()
    mcp = create_mcp_server(state)

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match='not initialized'):
            await client.call_tool(
                'create_capability', {'tenant_id': tenant_id, 'data': {'name': 'X'}}
            )
