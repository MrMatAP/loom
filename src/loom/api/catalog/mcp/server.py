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
from loom.api.catalog.dataproduct.repository import DataProductRepository
from loom.api.catalog.dataproduct.schemas import (
    DataProductCreateRequest,
    DataProductLineageCreateRequest,
)
from loom.api.catalog.dataproduct.service import DataProductService
from loom.api.catalog.datasource.repository import DataSourceRepository
from loom.api.catalog.datasource.schemas import DataSourceCreateRequest
from loom.api.catalog.datasource.service import DataSourceService
from loom.api.catalog.dependencies import get_principal_in_tenant
from loom.api.catalog.environment.repository import EnvironmentRepository
from loom.api.catalog.environment.service import EnvironmentService
from loom.api.catalog.model_endpoint.repository import ModelEndpointRepository
from loom.api.catalog.model_endpoint.schemas import ModelEndpointCreateRequest
from loom.api.catalog.model_endpoint.service import ModelEndpointService
from loom.api.catalog.pagination import Page
from loom.api.catalog.security import (
    AuthenticatedPrincipal,
    AuthenticationError,
    TokenValidator,
    assert_scopes,
    expand_claims_to_scopes,
)
from loom.api.catalog.skill.application_service import SkillApplicationService
from loom.api.catalog.skill.schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)
from loom.api.catalog.tool.repository import ToolRepository
from loom.api.catalog.tool.schemas import (
    ToolCreateRequest,
    ToolDataBindingCreateRequest,
)
from loom.api.catalog.tool.service import ToolService
from loom.domain.enums import LifecycleState
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.agent import AgentRead
from loom.schemas.capability import CapabilityRead
from loom.schemas.dataproduct import DataProductLineageRead, DataProductRead
from loom.schemas.datasource import DataSourceRead
from loom.schemas.environment import (
    EnvironmentCreate,
    EnvironmentRead,
    EnvironmentUpdate,
)
from loom.schemas.model_endpoint import ModelEndpointRead
from loom.schemas.skill import SkillGraphEdgeRead, SkillGraphNodeRead, SkillRead
from loom.schemas.tool import ToolDataBindingRead, ToolRead


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
    state: McpState, session: AsyncSession, *, tenant_id: uuid.UUID
) -> AuthenticatedPrincipal:
    """The MCP-side equivalent of `dependencies.get_current_principal`:
    same token validation and Principal resolution
    (`dependencies.get_principal_in_tenant`), just reached from a tool
    function instead of FastAPI dependency injection. `tenant_id` is a
    required argument on every tool (see `_register_resource_tools`
    below), same reasoning as the REST routes' `/tenants/{tenant_id}/...`
    path -- an identity provisioned in more than one Tenant targets a
    specific one explicitly rather than through an ambiguous lookup."""
    if state.token_validator is None:
        raise RuntimeError('McpState not initialized: token_validator is unset')
    token = _bearer_token()
    if token is None:
        raise AuthenticationError('Missing bearer token')
    try:
        claims = state.token_validator.decode(token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError(f'Invalid token: {exc}') from exc
    return await get_principal_in_tenant(tenant_id, claims, session)


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
    ResourceBinding(
        label='tool',
        plural='tools',
        article='a',
        service_cls=ToolService,
        repository_cls=ToolRepository,
        create_request_cls=ToolCreateRequest,
        read_cls=ToolRead,
        read_scope='catalog:tool:read',
        write_scope='catalog:tool:write',
        transition_scope='catalog:tool:transition',
    ),
    ResourceBinding(
        label='datasource',
        plural='datasources',
        article='a',
        service_cls=DataSourceService,
        repository_cls=DataSourceRepository,
        create_request_cls=DataSourceCreateRequest,
        read_cls=DataSourceRead,
        read_scope='catalog:datasource:read',
        write_scope='catalog:datasource:write',
        transition_scope='catalog:datasource:transition',
    ),
    ResourceBinding(
        label='dataproduct',
        plural='dataproducts',
        article='a',
        service_cls=DataProductService,
        repository_cls=DataProductRepository,
        create_request_cls=DataProductCreateRequest,
        read_cls=DataProductRead,
        read_scope='catalog:dataproduct:read',
        write_scope='catalog:dataproduct:write',
        transition_scope='catalog:dataproduct:transition',
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
        session: AsyncSession, *, scope: str, tenant_id: uuid.UUID
    ) -> AuthenticatedPrincipal:
        principal = await _authenticated_principal(state, session, tenant_id=tenant_id)
        assert_scopes(principal.scopes, scope)
        return principal

    @mcp.tool(
        name=f'create_{binding.label}',
        description=f'Create {binding.article} {binding.label.title()} in tenant_id.',
    )
    async def _create(
        tenant_id: uuid.UUID, data: binding.create_request_cls
    ) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope=binding.write_scope, tenant_id=tenant_id
            )
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
        description=f'Get the current version of {binding.article} {binding.label.title()} in tenant_id.',
    )
    async def _get(tenant_id: uuid.UUID, entity_id: uuid.UUID) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope=binding.read_scope, tenant_id=tenant_id
            )
            service = binding.service_cls(binding.repository_cls(session))
            found = await service.get_current(principal.tenant_id, entity_id)
            return binding.read_cls.model_validate(found)

    @mcp.tool(
        name=f'list_{binding.plural}',
        description=(
            f'List current versions of {binding.plural.title()} in tenant_id, '
            'optionally filtered by lifecycle_state.'
        ),
    )
    async def _list(
        tenant_id: uuid.UUID,
        lifecycle_state: LifecycleState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[binding.read_cls]:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope=binding.read_scope, tenant_id=tenant_id
            )
            service = binding.service_cls(binding.repository_cls(session))
            items, total = await service.list_current(
                principal.tenant_id,
                lifecycle_state=lifecycle_state,
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
        description=f'List every version of {binding.article} {binding.label.title()} in tenant_id.',
    )
    async def _versions(
        tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[binding.read_cls]:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope=binding.read_scope, tenant_id=tenant_id
            )
            service = binding.service_cls(binding.repository_cls(session))
            versions = await service.list_versions(principal.tenant_id, entity_id)
            return [binding.read_cls.model_validate(v) for v in versions]

    @mcp.tool(
        name=f'get_{binding.label}_version',
        description=f'Get one specific version of {binding.article} {binding.label.title()} in tenant_id.',
    )
    async def _get_version(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope=binding.read_scope, tenant_id=tenant_id
            )
            service = binding.service_cls(binding.repository_cls(session))
            found = await service.get_version(principal.tenant_id, entity_id, version)
            return binding.read_cls.model_validate(found)

    @mcp.tool(
        name=f'update_{binding.label}',
        description=(
            f'Create a new version of {binding.article} {binding.label.title()} in '
            'tenant_id -- entities in this registry are append-only/versioned, so '
            '"update" means a new version row, not an in-place mutation of the '
            'current one.'
        ),
    )
    async def _update(
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: binding.create_request_cls,  # type: ignore[name-defined]
    ) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope=binding.write_scope, tenant_id=tenant_id
            )
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
        description=f'Transition {binding.article} {binding.label.title()} in tenant_id to a new lifecycle state.',
    )
    async def _transition(
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
    ) -> binding.read_cls:  # type: ignore[name-defined]
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope=binding.transition_scope, tenant_id=tenant_id
            )
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


def _register_skill_tools(mcp: FastMCP, state: McpState) -> None:
    """Register every Skill tool: the same create/get/list/versions/
    update/transition six `_register_resource_tools` gives the other six
    resources, plus `add_skill_node`/`list_skill_nodes`/`add_skill_edge`/
    `list_skill_edges` for its graph sub-resource. Skill gets its own
    registration function, not a `_BINDINGS` entry, because it no longer
    goes through a bare `Service(Repository(session))` pair -- it's
    `SkillApplicationService(UnitOfWork(session))` now (see
    docs/adr/0001-ddd-separation-for-catalog-domain.md), and the graph
    tools are keyed on `(entity_id, version)` rather than `entity_id`
    alone either way, so they never fit `_register_resource_tools`'s
    shape (mirrors `skill/router.py`'s routes)."""

    async def _principal(
        session: AsyncSession, *, scope: str, tenant_id: uuid.UUID
    ) -> AuthenticatedPrincipal:
        principal = await _authenticated_principal(state, session, tenant_id=tenant_id)
        assert_scopes(principal.scopes, scope)
        return principal

    @mcp.tool(description='Create a Skill in tenant_id.')
    async def create_skill(tenant_id: uuid.UUID, data: SkillCreateRequest) -> SkillRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:write', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            result = await svc.create(
                tenant_id=principal.tenant_id,
                created_by_id=principal.principal_id,
                data=data,
            )
            await session.commit()
            return result

    @mcp.tool(description='Get the current version of a Skill in tenant_id.')
    async def get_skill(tenant_id: uuid.UUID, entity_id: uuid.UUID) -> SkillRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:read', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            return await svc.get_current(principal.tenant_id, entity_id)

    @mcp.tool(
        description='List current versions of Skills in tenant_id, optionally '
        'filtered by lifecycle_state.'
    )
    async def list_skills(
        tenant_id: uuid.UUID,
        lifecycle_state: LifecycleState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[SkillRead]:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:read', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            items, total = await svc.list_current(
                principal.tenant_id,
                lifecycle_state=lifecycle_state,
                limit=limit,
                offset=offset,
            )
            return Page[SkillRead](items=items, total=total, limit=limit, offset=offset)

    @mcp.tool(description='List every version of a Skill in tenant_id.')
    async def list_skill_versions(
        tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[SkillRead]:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:read', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            return await svc.list_versions(principal.tenant_id, entity_id)

    @mcp.tool(description='Get one specific version of a Skill in tenant_id.')
    async def get_skill_version(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> SkillRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:read', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            return await svc.get_version(principal.tenant_id, entity_id, version)

    @mcp.tool(
        description=(
            'Create a new version of a Skill in tenant_id -- entities in this '
            'registry are append-only/versioned, so "update" means a new version '
            'row, not an in-place mutation of the current one.'
        )
    )
    async def update_skill(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, data: SkillCreateRequest
    ) -> SkillRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:write', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            result = await svc.create_new_version(
                tenant_id=principal.tenant_id,
                created_by_id=principal.principal_id,
                entity_id=entity_id,
                data=data,
            )
            await session.commit()
            return result

    @mcp.tool(description='Transition a Skill in tenant_id to a new lifecycle state.')
    async def transition_skill(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int, to_state: LifecycleState
    ) -> SkillRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:transition', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            result = await svc.transition(
                tenant_id=principal.tenant_id,
                entity_id=entity_id,
                version=version,
                to_state=to_state,
                actor_id=principal.principal_id,
            )
            await session.commit()
            return result

    @mcp.tool(description="Add a node to one version of a Skill's graph in tenant_id.")
    async def add_skill_node(
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphNodeCreateRequest,
    ) -> SkillGraphNodeRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:write', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            result = await svc.add_node(
                tenant_id=principal.tenant_id,
                entity_id=entity_id,
                version=version,
                data=data,
            )
            await session.commit()
            return result

    @mcp.tool(
        description="List the nodes of one version of a Skill's graph in tenant_id."
    )
    async def list_skill_nodes(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphNodeRead]:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:read', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            return await svc.list_nodes(principal.tenant_id, entity_id, version)

    @mcp.tool(description="Add an edge to one version of a Skill's graph in tenant_id.")
    async def add_skill_edge(
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphEdgeCreateRequest,
    ) -> SkillGraphEdgeRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:write', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            result = await svc.add_edge(
                tenant_id=principal.tenant_id,
                entity_id=entity_id,
                version=version,
                data=data,
            )
            await session.commit()
            return result

    @mcp.tool(
        description="List the edges of one version of a Skill's graph in tenant_id."
    )
    async def list_skill_edges(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphEdgeRead]:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:skill:read', tenant_id=tenant_id
            )
            svc = SkillApplicationService(UnitOfWork(session))
            return await svc.list_edges(principal.tenant_id, entity_id, version)


def _register_tool_data_binding_tools(mcp: FastMCP, state: McpState) -> None:
    """Register `add_tool_data_binding`/`list_tool_data_bindings` -- Tool's
    static, design-time-bound `Tool.data_bindings[]` (see CLAUDE.md's
    deterministic/non-deterministic data-access split), keyed on
    `(entity_id, version)` like the Skill graph tools above."""

    async def _principal(
        session: AsyncSession, *, scope: str, tenant_id: uuid.UUID
    ) -> AuthenticatedPrincipal:
        principal = await _authenticated_principal(state, session, tenant_id=tenant_id)
        assert_scopes(principal.scopes, scope)
        return principal

    @mcp.tool(description='Add a data binding to one version of a Tool in tenant_id.')
    async def add_tool_data_binding(
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: ToolDataBindingCreateRequest,
    ) -> ToolDataBindingRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:tool:write', tenant_id=tenant_id
            )
            service = ToolService(ToolRepository(session))
            binding = await service.add_data_binding(
                tenant_id=principal.tenant_id,
                entity_id=entity_id,
                version=version,
                data=data,
            )
            await session.commit()
            return ToolDataBindingRead.model_validate(binding)

    @mcp.tool(
        description=('List the data bindings of one version of a Tool in tenant_id.')
    )
    async def list_tool_data_bindings(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[ToolDataBindingRead]:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:tool:read', tenant_id=tenant_id
            )
            service = ToolService(ToolRepository(session))
            bindings = await service.list_data_bindings(
                principal.tenant_id, entity_id, version
            )
            return [ToolDataBindingRead.model_validate(b) for b in bindings]


def _register_dataproduct_lineage_tools(mcp: FastMCP, state: McpState) -> None:
    """Register `add_dataproduct_lineage`/`list_dataproduct_lineage` --
    DataProduct lineage back to its source DataSource(s)/DataProduct(s),
    keyed on `(entity_id, version)` like the two sub-resource groups
    above."""

    async def _principal(
        session: AsyncSession, *, scope: str, tenant_id: uuid.UUID
    ) -> AuthenticatedPrincipal:
        principal = await _authenticated_principal(state, session, tenant_id=tenant_id)
        assert_scopes(principal.scopes, scope)
        return principal

    @mcp.tool(
        description=('Add a lineage edge to one version of a DataProduct in tenant_id.')
    )
    async def add_dataproduct_lineage(
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: DataProductLineageCreateRequest,
    ) -> DataProductLineageRead:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:dataproduct:write', tenant_id=tenant_id
            )
            service = DataProductService(DataProductRepository(session))
            lineage = await service.add_lineage(
                tenant_id=principal.tenant_id,
                entity_id=entity_id,
                version=version,
                data=data,
            )
            await session.commit()
            return DataProductLineageRead.model_validate(lineage)

    @mcp.tool(
        description=(
            'List the lineage edges of one version of a DataProduct in tenant_id.'
        )
    )
    async def list_dataproduct_lineage(
        tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[DataProductLineageRead]:
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            principal = await _principal(
                session, scope='catalog:dataproduct:read', tenant_id=tenant_id
            )
            service = DataProductService(DataProductRepository(session))
            lineage = await service.list_lineage(
                principal.tenant_id, entity_id, version
            )
            return [DataProductLineageRead.model_validate(l) for l in lineage]


async def _scopes_from_bearer_token(state: McpState) -> frozenset[str]:
    """Decode the request's bearer token and expand its claims to scopes,
    without resolving a Principal -- the Environment tools' auth path
    mirrors `environment/router.py`'s own `require_scopes(...)` dependency
    exactly, which (unlike every other router) never calls
    `get_current_principal`: `tenant_id` there is taken straight from the
    path, not cross-checked against a Principal row in that Tenant. See
    `dependencies.require_scopes`, the REST-side equivalent."""
    if state.token_validator is None:
        raise RuntimeError('McpState not initialized: token_validator is unset')
    token = _bearer_token()
    if token is None:
        raise AuthenticationError('Missing bearer token')
    try:
        claims = state.token_validator.decode(token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError(f'Invalid token: {exc}') from exc
    return expand_claims_to_scopes(claims)


def _register_environment_tools(mcp: FastMCP, state: McpState) -> None:
    """Register `create_environment`/`get_environment`/`list_environments`/
    `update_environment` -- Environment is platform-tier and not a
    VersionedEntity (no lifecycle_state/version/transitions), so it fits
    neither `ResourceBinding` (Capability/ModelEndpoint/.../DataProduct)
    nor the `(entity_id, version)`-keyed sub-resource groups above (mirrors
    `environment/router.py`)."""

    async def _require_scope(scope: str) -> None:
        scopes = await _scopes_from_bearer_token(state)
        assert_scopes(scopes, scope)

    @mcp.tool(description='Create an Environment in tenant_id.')
    async def create_environment(
        tenant_id: uuid.UUID, data: EnvironmentCreate
    ) -> EnvironmentRead:
        await _require_scope('catalog:environment:write')
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            service = EnvironmentService(EnvironmentRepository(session))
            created = await service.create(tenant_id, data)
            await session.commit()
            return EnvironmentRead.model_validate(created)

    @mcp.tool(description='Get one Environment in tenant_id.')
    async def get_environment(
        tenant_id: uuid.UUID, environment_id: uuid.UUID
    ) -> EnvironmentRead:
        await _require_scope('catalog:environment:read')
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            service = EnvironmentService(EnvironmentRepository(session))
            found = await service.get(tenant_id, environment_id)
            return EnvironmentRead.model_validate(found)

    @mcp.tool(description='List Environments in tenant_id.')
    async def list_environments(
        tenant_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Page[EnvironmentRead]:
        await _require_scope('catalog:environment:read')
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            service = EnvironmentService(EnvironmentRepository(session))
            items, total = await service.list_by_tenant(
                tenant_id, limit=limit, offset=offset
            )
            return Page[EnvironmentRead](
                items=[EnvironmentRead.model_validate(item) for item in items],
                total=total,
                limit=limit,
                offset=offset,
            )

    @mcp.tool(
        description=(
            "Update an Environment's boundary refs in tenant_id in place "
            '-- Environment mutates in place, unlike the versioned '
            'entities above.'
        )
    )
    async def update_environment(
        tenant_id: uuid.UUID, environment_id: uuid.UUID, data: EnvironmentUpdate
    ) -> EnvironmentRead:
        await _require_scope('catalog:environment:write')
        session_factory = _require_session_factory(state)
        async with session_factory() as session:
            service = EnvironmentService(EnvironmentRepository(session))
            updated = await service.update(tenant_id, environment_id, data)
            await session.commit()
            return EnvironmentRead.model_validate(updated)


def create_mcp_server(state: McpState) -> FastMCP:
    """Build the Catalog MCP server: create/get/list/versions/update/
    transition tools for every content-plane resource (Capability,
    ModelEndpoint, Agent, Skill, Tool, DataSource, DataProduct), plus each
    resource's own sub-resource tools (Skill graph nodes/edges, Tool data
    bindings, DataProduct lineage) -- an alternative interface onto the
    same Service layer the REST API's routers call (see
    docs/superpowers/specs/2026-08-08-catalog-api-design.md), plus CRUD
    tools for the platform-tier, non-versioned Environment. Tenant/
    Principal are deliberately left off this server -- they're
    bootstrapping routes (`loom tenant`/`loom principal` already cover
    them over REST) with a different auth shape than every tool above
    (`_authenticated_principal` always resolves a Principal *in*
    `tenant_id`; `POST /tenants`/`POST /principals` intentionally don't
    require one -- see `cli/catalog.py`'s Tenant/Principal section for
    why)."""
    mcp = FastMCP('Loom Catalog')
    for binding in _BINDINGS:
        _register_resource_tools(mcp, state, binding)
    _register_skill_tools(mcp, state)
    _register_tool_data_binding_tools(mcp, state)
    _register_dataproduct_lineage_tools(mcp, state)
    _register_environment_tools(mcp, state)
    return mcp
