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
from loom.model.schemas.model_endpoint import ModelEndpointRead

from .repository import ModelEndpointRepository
from .schemas import ModelEndpointCreateRequest
from .service import ModelEndpointService

router = APIRouter(prefix='/model-endpoints', tags=['model-endpoints'])


def _service(session: AsyncSession = Depends(get_session)) -> ModelEndpointService:
    return ModelEndpointService(ModelEndpointRepository(session))


@router.post('', response_model=ModelEndpointRead, status_code=201)
async def create_model_endpoint(
    body: ModelEndpointCreateRequest,
    service: ModelEndpointService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:model_endpoint:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[ModelEndpointRead])
async def list_model_endpoints(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: ModelEndpointService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:model_endpoint:read')),
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


@router.get('/{entity_id}', response_model=ModelEndpointRead)
async def get_model_endpoint(
    entity_id: uuid.UUID,
    service: ModelEndpointService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:model_endpoint:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[ModelEndpointRead])
async def list_model_endpoint_versions(
    entity_id: uuid.UUID,
    service: ModelEndpointService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:model_endpoint:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=ModelEndpointRead)
async def get_model_endpoint_version(
    entity_id: uuid.UUID,
    version: int,
    service: ModelEndpointService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:model_endpoint:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=ModelEndpointRead, status_code=201)
async def create_model_endpoint_version(
    entity_id: uuid.UUID,
    body: ModelEndpointCreateRequest,
    service: ModelEndpointService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:model_endpoint:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post(
    '/{entity_id}/versions/{version}/transitions', response_model=ModelEndpointRead
)
async def transition_model_endpoint(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: ModelEndpointService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:model_endpoint:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )
