import dataclasses

import jwt
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from loom.api.catalog.agent.repository import AgentRepository
from loom.api.catalog.agent.schemas import AgentCreateRequest
from loom.api.catalog.agent.service import AgentService
from loom.api.catalog.capability.repository import CapabilityRepository
from loom.api.catalog.capability.schemas import CapabilityCreateRequest
from loom.api.catalog.capability.service import CapabilityService
from loom.api.catalog.dependencies import resolve_principal
from loom.api.catalog.model_endpoint.repository import ModelEndpointRepository
from loom.api.catalog.model_endpoint.schemas import ModelEndpointCreateRequest
from loom.api.catalog.model_endpoint.service import ModelEndpointService
from loom.api.catalog.security import (
    AuthenticatedPrincipal,
    AuthenticationError,
    TokenValidator,
    assert_scopes,
)
from loom.model.schemas.agent import AgentRead
from loom.model.schemas.capability import CapabilityRead
from loom.model.schemas.model_endpoint import ModelEndpointRead


@dataclasses.dataclass
class McpState:
    """`session_factory`/`token_validator` are constructed during the
    hosting FastAPI app's lifespan (see `mcp/main.py`), same as
    `app.state.session_factory`/`app.state.token_validator` on the REST
    app -- `TokenValidator.__init__` resolves the JWKS URI over the
    network, so it must not run at import time. Tool closures below hold a
    reference to this object (not its attributes) so they keep working
    once the lifespan populates it."""

    session_factory: async_sessionmaker[AsyncSession] | None = None
    token_validator: TokenValidator | None = None


def _require_session_factory(state: McpState) -> async_sessionmaker[AsyncSession]:
    """Guard against a tool call reaching a server whose lifespan never
    ran (e.g. the ASGI app was mounted but the process never started up)
    with a clear error instead of an `AttributeError` deep in a tool body.
    A plain `if`/`raise`, not `assert`, so this still holds under `-O`."""
    if state.session_factory is None:
        raise RuntimeError('McpState not initialized: session_factory is unset')
    return state.session_factory


def _bearer_token() -> str | None:
    """The bearer token off the underlying HTTP request, if any. Mirrors
    `dependencies.oauth2_scheme`'s extraction for the REST path -- MCP tool
    functions don't get FastAPI's `Depends()` machinery, so this reaches
    the request through fastmcp's own context accessor instead.
    `get_http_headers` returns `{}` (never raises) when there's no request
    in scope, e.g. the in-memory transport used in tests."""
    headers = get_http_headers(include={'authorization'})
    scheme, _, token = headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token:
        return None
    return token


async def _authenticated_principal(
    state: McpState, session: AsyncSession
) -> AuthenticatedPrincipal:
    """The MCP-side equivalent of `dependencies.get_current_principal`:
    same token validation and Principal resolution
    (`dependencies.resolve_principal`), just reached from a tool function
    instead of FastAPI dependency injection."""
    if state.token_validator is None:
        raise RuntimeError('McpState not initialized: token_validator is unset')
    token = _bearer_token()
    if token is None:
        raise AuthenticationError('Missing bearer token')
    try:
        claims = state.token_validator.decode(token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError(f'Invalid token: {exc}') from exc
    return await resolve_principal(claims, session)


def create_mcp_server(state: McpState) -> FastMCP:
    """Build the Catalog MCP server: one tool per REST `create` endpoint,
    each calling the same Service layer its router counterpart calls (see
    docs/superpowers/specs/2026-08-08-catalog-api-design.md) so business
    logic -- including tenant/scope enforcement -- isn't duplicated or
    allowed to drift between the two adapters."""
    mcp = FastMCP('Loom Catalog')

    @mcp.tool
    async def create_capability(data: CapabilityCreateRequest) -> CapabilityRead:
        """Create a Capability."""
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _authenticated_principal(state, session)
            assert_scopes(principal.scopes, 'catalog:capability:write')
            service = CapabilityService(CapabilityRepository(session))
            capability = await service.create(
                tenant_id=principal.tenant_id,
                created_by_id=principal.principal_id,
                data=data,
            )
            await session.commit()
            return CapabilityRead.model_validate(capability)

    @mcp.tool
    async def create_model(data: ModelEndpointCreateRequest) -> ModelEndpointRead:
        """Create a ModelEndpoint."""
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _authenticated_principal(state, session)
            assert_scopes(principal.scopes, 'catalog:model_endpoint:write')
            service = ModelEndpointService(ModelEndpointRepository(session))
            model_endpoint = await service.create(
                tenant_id=principal.tenant_id,
                created_by_id=principal.principal_id,
                data=data,
            )
            await session.commit()
            return ModelEndpointRead.model_validate(model_endpoint)

    @mcp.tool
    async def create_agent(data: AgentCreateRequest) -> AgentRead:
        """Create an Agent."""
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _authenticated_principal(state, session)
            assert_scopes(principal.scopes, 'catalog:agent:write')
            service = AgentService(AgentRepository(session))
            agent = await service.create(
                tenant_id=principal.tenant_id,
                created_by_id=principal.principal_id,
                data=data,
            )
            await session.commit()
            return AgentRead.model_validate(agent)

    return mcp
