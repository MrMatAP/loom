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
from loom.model.schemas.capability import CapabilityRead, CapabilityRealizationRead

from .repository import CapabilityRepository
from .schemas import CapabilityCreateRequest, CapabilityRealizationCreateRequest
from .service import CapabilityService

router = APIRouter(prefix='/tenants/{tenant_id}/capabilities', tags=['capabilities'])


def _service(session: AsyncSession = Depends(get_session)) -> CapabilityService:
    return CapabilityService(CapabilityRepository(session))


@router.post('', response_model=CapabilityRead, status_code=201)
async def create_capability(
    body: CapabilityCreateRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[CapabilityRead])
async def list_capabilities(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get('/{entity_id}', response_model=CapabilityRead)
async def get_capability(
    entity_id: uuid.UUID,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[CapabilityRead])
async def list_capability_versions(
    entity_id: uuid.UUID,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=CapabilityRead)
async def get_capability_version(
    entity_id: uuid.UUID,
    version: int,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=CapabilityRead, status_code=201)
async def create_capability_version(
    entity_id: uuid.UUID,
    body: CapabilityCreateRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post(
    '/{entity_id}/versions/{version}/transitions', response_model=CapabilityRead
)
async def transition_capability(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )


@router.post(
    '/{entity_id}/versions/{version}/realizations',
    response_model=CapabilityRealizationRead,
    status_code=201,
)
async def add_capability_realization(
    entity_id: uuid.UUID,
    version: int,
    body: CapabilityRealizationCreateRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:write')),
):
    return await service.add_realization(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/realizations',
    response_model=list[CapabilityRealizationRead],
)
async def list_capability_realizations(
    entity_id: uuid.UUID,
    version: int,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.list_realizations(principal.tenant_id, entity_id, version)
