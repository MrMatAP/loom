import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import (
    get_current_principal,
    get_session,
    require_scopes,
)
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.tool import ToolDataBindingRead, ToolRead

from .repository import ToolRepository
from .schemas import ToolCreateRequest, ToolDataBindingCreateRequest
from .service import ToolService

router = APIRouter(prefix='/tools', tags=['tools'])


def _service(session: AsyncSession = Depends(get_session)) -> ToolService:
    return ToolService(ToolRepository(session))


@router.post('', response_model=ToolRead, status_code=201)
async def create_tool(
    body: ToolCreateRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[ToolRead])
async def list_tools(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get('/{entity_id}', response_model=ToolRead)
async def get_tool(
    entity_id: uuid.UUID,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[ToolRead])
async def list_tool_versions(
    entity_id: uuid.UUID,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=ToolRead)
async def get_tool_version(
    entity_id: uuid.UUID,
    version: int,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=ToolRead, status_code=201)
async def create_tool_version(
    entity_id: uuid.UUID,
    body: ToolCreateRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=ToolRead)
async def transition_tool(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )


@router.post(
    '/{entity_id}/versions/{version}/data-bindings',
    response_model=ToolDataBindingRead,
    status_code=201,
)
async def add_tool_data_binding(
    entity_id: uuid.UUID,
    version: int,
    body: ToolDataBindingCreateRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:write')),
):
    return await service.add_data_binding(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/data-bindings',
    response_model=list[ToolDataBindingRead],
)
async def list_tool_data_bindings(
    entity_id: uuid.UUID,
    version: int,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.list_data_bindings(principal.tenant_id, entity_id, version)
