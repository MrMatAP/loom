import dataclasses
import uuid

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
from loom.api.catalog.pagination import Page
from loom.api.catalog.security import (
    AuthenticatedPrincipal,
    AuthenticationError,
    TokenValidator,
    assert_scopes,
)
from loom.model.enums import LifecycleState
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


@dataclasses.dataclass(frozen=True)
class ResourceBinding:
    """Everything that differs between Capability/ModelEndpoint/Agent, so
    `_register_resource_tools` below can register the same six tools --
    identical in shape across all three, since `*Service`'s
    get_current/get_version/list_versions/list_current/create_new_version/
    transition signatures are identical -- once per resource instead of
    six near-duplicate functions written out three times each.

    Scope names are copy-pasted verbatim from each resource's router
    (`require_scopes('catalog:...')` there), not derived, so a typo here
    can't silently create a mismatched permission boundary between REST
    and MCP."""

    label: str
    plural: str
    article: str  # 'a' or 'an', for the generated tool descriptions
    service_cls: type
    repository_cls: type
    create_request_cls: type
    read_cls: type
    read_scope: str
    write_scope: str
    transition_scope: str


_BINDINGS = (
    ResourceBinding(
        label='capability',
        plural='capabilities',
        article='a',
        service_cls=CapabilityService,
        repository_cls=CapabilityRepository,
        create_request_cls=CapabilityCreateRequest,
        read_cls=CapabilityRead,
        read_scope='catalog:capability:read',
        write_scope='catalog:capability:write',
        transition_scope='catalog:capability:transition',
    ),
    ResourceBinding(
        label='model',
        plural='models',
        article='a',
        service_cls=ModelEndpointService,
        repository_cls=ModelEndpointRepository,
        create_request_cls=ModelEndpointCreateRequest,
        read_cls=ModelEndpointRead,
        read_scope='catalog:model_endpoint:read',
        write_scope='catalog:model_endpoint:write',
        transition_scope='catalog:model_endpoint:transition',
    ),
    ResourceBinding(
        label='agent',
        plural='agents',
        article='an',
        service_cls=AgentService,
        repository_cls=AgentRepository,
        create_request_cls=AgentCreateRequest,
        read_cls=AgentRead,
        read_scope='catalog:agent:read',
        write_scope='catalog:agent:write',
        transition_scope='catalog:agent:transition',
    ),
)


def _register_resource_tools(
    mcp: FastMCP, state: McpState, binding: ResourceBinding
) -> None:
    """Register the six create/get/list/versions/update/transition tools
    for one resource. Each tool body follows the exact same shape as the
    corresponding router endpoint: resolve the principal, enforce the
    matching scope, call the Service, return the Read schema -- no HTTP
    hop, no duplicated business logic (see
    docs/superpowers/specs/2026-08-08-catalog-api-design.md)."""

    async def _principal(
        session: AsyncSession, *, scope: str
    ) -> AuthenticatedPrincipal:
        principal = await _authenticated_principal(state, session)
        assert_scopes(principal.scopes, scope)
        return principal

    @mcp.tool(
        name=f'create_{binding.label}',
        description=f'Create {binding.article} {binding.label.title()}.',
    )
    async def _create(data: binding.create_request_cls) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(session, scope=binding.write_scope)
            service = binding.service_cls(binding.repository_cls(session))
            created = await service.create(
                tenant_id=principal.tenant_id,
                created_by_id=principal.principal_id,
                data=data,
            )
            await session.commit()
            return binding.read_cls.model_validate(created)

    @mcp.tool(
        name=f'get_{binding.label}',
        description=f'Get the current version of {binding.article} {binding.label.title()}.',
    )
    async def _get(entity_id: uuid.UUID) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(session, scope=binding.read_scope)
            service = binding.service_cls(binding.repository_cls(session))
            found = await service.get_current(principal.tenant_id, entity_id)
            return binding.read_cls.model_validate(found)

    @mcp.tool(
        name=f'list_{binding.plural}',
        description=(
            f'List current versions of {binding.plural.title()} in this tenant, '
            'optionally filtered by lifecycle_state/slug.'
        ),
    )
    async def _list(
        lifecycle_state: LifecycleState | None = None,
        slug: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[binding.read_cls]:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(session, scope=binding.read_scope)
            service = binding.service_cls(binding.repository_cls(session))
            items, total = await service.list_current(
                principal.tenant_id,
                lifecycle_state=lifecycle_state,
                slug=slug,
                limit=limit,
                offset=offset,
            )
            return Page[binding.read_cls](
                items=[binding.read_cls.model_validate(item) for item in items],
                total=total,
                limit=limit,
                offset=offset,
            )

    @mcp.tool(
        name=f'list_{binding.label}_versions',
        description=f'List every version of {binding.article} {binding.label.title()}.',
    )
    async def _versions(entity_id: uuid.UUID) -> list[binding.read_cls]:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(session, scope=binding.read_scope)
            service = binding.service_cls(binding.repository_cls(session))
            versions = await service.list_versions(principal.tenant_id, entity_id)
            return [binding.read_cls.model_validate(v) for v in versions]

    @mcp.tool(
        name=f'get_{binding.label}_version',
        description=f'Get one specific version of {binding.article} {binding.label.title()}.',
    )
    async def _get_version(entity_id: uuid.UUID, version: int) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(session, scope=binding.read_scope)
            service = binding.service_cls(binding.repository_cls(session))
            found = await service.get_version(principal.tenant_id, entity_id, version)
            return binding.read_cls.model_validate(found)

    @mcp.tool(
        name=f'update_{binding.label}',
        description=(
            f'Create a new version of {binding.article} {binding.label.title()} -- '
            'entities in this registry are append-only/versioned, so "update" '
            'means a new version row, not an in-place mutation of the current one.'
        ),
    )
    async def _update(
        entity_id: uuid.UUID,
        data: binding.create_request_cls,  # type: ignore[name-defined]
    ) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(session, scope=binding.write_scope)
            service = binding.service_cls(binding.repository_cls(session))
            updated = await service.create_new_version(
                tenant_id=principal.tenant_id,
                created_by_id=principal.principal_id,
                entity_id=entity_id,
                data=data,
            )
            await session.commit()
            return binding.read_cls.model_validate(updated)

    @mcp.tool(
        name=f'transition_{binding.label}',
        description=f'Transition {binding.article} {binding.label.title()} to a new lifecycle state.',
    )
    async def _transition(
        entity_id: uuid.UUID, version: int, to_state: LifecycleState
    ) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(session, scope=binding.transition_scope)
            service = binding.service_cls(binding.repository_cls(session))
            transitioned = await service.transition(
                tenant_id=principal.tenant_id,
                entity_id=entity_id,
                version=version,
                to_state=to_state,
                actor_id=principal.principal_id,
            )
            await session.commit()
            return binding.read_cls.model_validate(transitioned)


def create_mcp_server(state: McpState) -> FastMCP:
    """Build the Catalog MCP server: create/get/list/versions/update/
    transition tools for Capability, ModelEndpoint, and Agent -- an
    alternative interface onto the same Service layer the REST API's
    routers call (see
    docs/superpowers/specs/2026-08-08-catalog-api-design.md)."""
    mcp = FastMCP('Loom Catalog')
    for binding in _BINDINGS:
        _register_resource_tools(mcp, state, binding)
    return mcp
