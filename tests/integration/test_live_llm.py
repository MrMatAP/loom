"""Functional test: Capability -> ModelEndpoint -> Agent, driven through
the real Catalog REST routes end to end (real routers, real Services, real
repositories), then handed off to a real LangChain agent (`langchain.
agents.create_agent`, LangGraph-backed under the hood) that calls the
live model server the ModelEndpoint describes.

Auth is bypassed here (a fixed, fully-scoped `AuthenticatedPrincipal` via
dependency overrides), same as `test_claim_chain.py::
test_live_token_authorizes_a_real_api_request` -- the real IdP is already
covered by the `live_idp` suite, and isn't the live dependency this one is
about. Fixtures are duplicated from `tests/api/conftest.py` rather than
imported: pytest fixtures don't cross sibling directories
(`tests/integration/` vs `tests/api/`).

`ModelEndpoint.base_url`'s exact contract (bare origin vs. including the
OpenAI-compatible `/v1` path) isn't pinned down anywhere else in this
codebase (see `src/loom/model/schemas/model_endpoint.py`). This suite
stores the bare origin the user gave us (`http://localhost:1234`) and
appends `/v1` only when constructing the LangChain client below -- that's
this suite's own reading, not a documented invariant. Worth settling
properly if a real Execution Engine ever needs to make the same call.

CapabilityRealization (Agent/Skill/Tool -> Capability, weighted) has no
API surface yet (see docs/architecture.md's "What's implemented"). This
suite creates a Capability to prove the entity exists and is reachable
through the same Tenant, but can't link it to the Agent through any
exposed route -- that's a product gap, not something this test papers
over.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from loom.api.catalog.dependencies import (
    get_current_principal,
    get_current_token,
    get_session,
)
from loom.api.catalog.main import create_app
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.config import RootConfig
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.model import (
    agent,  # noqa: F401 -- table registration side effect
    capability,  # noqa: F401
    model_endpoint,  # noqa: F401
)
from loom.model.base import Base
from loom.model.enums import PrincipalKind
from loom.model.tenant import Principal, Tenant

from .live_llm import live_llm_base_url, probe_live_llm

pytestmark = [pytest.mark.live_llm]

# Self-skips this whole module -- with a message naming the missing
# package -- when the `live-llm` dependency group isn't installed
# (`uv sync --group live-llm`), rather than an ImportError deep in a test.
pytest.importorskip('langchain')
pytest.importorskip('langchain_openai')

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

ALL_SCOPES = content_scopes() | platform_scopes()


@pytest.fixture(scope='module')
def live_model_id() -> str:
    """The id of whatever model is actually loaded on the live server --
    discovered, not assumed: we can't know what the user has running in
    LM Studio/vLLM/ollama. Skips (distinguishing "nothing listening" from
    "listening but no model loaded") rather than failing when it can't be
    resolved -- same reasoning as `live_idp`'s `admin_client`/`live_db`'s
    `live_db_engine`: an unavailable local dependency is an environment
    fact, not a test bug."""
    model_id, skip_reason = probe_live_llm(live_llm_base_url())
    if skip_reason is not None:
        pytest.skip(skip_reason)
    return model_id


@pytest_asyncio.fixture
async def async_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def fake_principal(async_session_factory) -> AuthenticatedPrincipal:
    """Real Tenant+Principal seeded in an in-memory DB, wrapped with every
    scope granted -- mirrors `tests/api/conftest.py::fake_principal`."""
    async with async_session_factory() as session:
        tenant = Tenant(slug='loom-it-llm', name='Loom LLM Integration Test Tenant')
        session.add(tenant)
        await session.flush()
        principal = Principal(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            external_id='loom-it-llm-user',
        )
        session.add(principal)
        await session.commit()
        return AuthenticatedPrincipal(
            principal_id=principal.id,
            tenant_id=tenant.id,
            scopes=frozenset(ALL_SCOPES),
        )


@pytest_asyncio.fixture
async def api_client(
    async_session_factory, fake_principal
) -> AsyncGenerator[AsyncClient]:
    """The real Catalog FastAPI app (real routers/Services/repositories),
    talked to in-process over `ASGITransport` -- only auth is bypassed
    (see module docstring). Mirrors `tests/api/conftest.py::api_client`."""
    app = create_app(RootConfig(config_path='/dev/null'))

    async def _override_session() -> AsyncGenerator[AsyncSession]:
        async with async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'loom-it-llm-user',
        'scope': ' '.join(sorted(ALL_SCOPES)),
    }
    app.dependency_overrides[get_current_principal] = lambda: fake_principal

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        yield client


@pytest.mark.asyncio
async def test_capability_model_agent_wired_to_a_live_llm(
    api_client, fake_principal, live_model_id
):
    """End to end: a Capability, a ModelEndpoint pointed at the live
    server, and an Agent bound to it -- each created through the real REST
    routes -- then the ModelEndpoint is resolved back the way the
    (not-yet-built) Execution Engine would, and handed to a real LangChain
    agent that actually calls the live model."""
    tenant_id = fake_principal.tenant_id
    base = f'/api/v1/tenants/{tenant_id}'

    capability_response = await api_client.post(
        f'{base}/capabilities',
        json={
            'name': 'Live LLM Smoke Test',
            'description': (
                'Proves a ModelEndpoint the Catalog describes is actually callable.'
            ),
            'target_metrics': [{'name': 'reachable', 'target': 1}],
        },
    )
    assert capability_response.status_code == 201, capability_response.text

    model_response = await api_client.post(
        f'{base}/model-endpoints',
        json={
            'name': 'Local LLM',
            'description': f'Live OpenAI-compatible server at {live_llm_base_url()}',
            'protocol': 'openai_compatible',
            'base_url': live_llm_base_url(),
            'model': live_model_id,
        },
    )
    assert model_response.status_code == 201, model_response.text
    model_entity_id = model_response.json()['entity_id']

    agent_response = await api_client.post(
        f'{base}/agents',
        json={
            'name': 'LangChain Test Agent',
            'description': 'Exercises the live ModelEndpoint via a real LangChain agent.',
            'layer': 'infra_ops',
            # Floating: holds ModelEndpoint.entity_id and always resolves
            # to whichever version is current -- see CLAUDE.md's entity
            # table on Agent.model_binding_id.
            'model_binding_id': model_entity_id,
            'llm_config': {},
            'prompt': 'Reply with exactly the single word PONG and nothing else.',
            'memory_scope': 'none',
        },
    )
    assert agent_response.status_code == 201, agent_response.text
    agent_payload = agent_response.json()
    assert agent_payload['model_binding_id'] == model_entity_id

    # Resolve the floating binding the way a real caller must: it's
    # ModelEndpoint.entity_id, not a version row id, so "get the bound
    # model" means "get its current version", not a row-id lookup.
    resolved_model_response = await api_client.get(
        f'{base}/model-endpoints/{agent_payload["model_binding_id"]}'
    )
    assert resolved_model_response.status_code == 200
    resolved_model = resolved_model_response.json()
    assert resolved_model['entity_id'] == model_entity_id
    assert resolved_model['model'] == live_model_id

    chat_model = ChatOpenAI(
        base_url=f'{resolved_model["base_url"]}/v1',
        api_key='not-needed',  # local OpenAI-compatible servers don't check this
        model=resolved_model['model'],
        timeout=120.0,
    )
    # `langchain.agents.create_agent` -- LangChain 1.0's agent builder,
    # LangGraph-backed under the hood (it compiles to the same
    # `CompiledStateGraph` `langgraph.prebuilt.create_react_agent` used to
    # return directly; the latter is deprecated as of LangGraph 1.0 in
    # favor of this one).
    chat_agent = create_agent(
        model=chat_model, tools=[], system_prompt=agent_payload['prompt']
    )

    result = await chat_agent.ainvoke({'messages': [('user', 'Reply now.')]})

    reply = result['messages'][-1].content
    assert isinstance(reply, str)
    assert reply.strip() != ''
