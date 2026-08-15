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
from loom.model.schemas.datasource import DataSourceRead

from .repository import DataSourceRepository
from .schemas import DataSourceCreateRequest
from .service import DataSourceService

router = APIRouter(prefix='/tenants/{tenant_id}/datasources', tags=['datasources'])


def _service(session: AsyncSession = Depends(get_session)) -> DataSourceService:
    return DataSourceService(DataSourceRepository(session))


@router.post('', response_model=DataSourceRead, status_code=201)
async def create_datasource(
    body: DataSourceCreateRequest,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[DataSourceRead])
async def list_datasources(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
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


@router.get('/{entity_id}', response_model=DataSourceRead)
async def get_datasource(
    entity_id: uuid.UUID,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[DataSourceRead])
async def list_datasource_versions(
    entity_id: uuid.UUID,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=DataSourceRead)
async def get_datasource_version(
    entity_id: uuid.UUID,
    version: int,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=DataSourceRead, status_code=201)
async def create_datasource_version(
    entity_id: uuid.UUID,
    body: DataSourceCreateRequest,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post(
    '/{entity_id}/versions/{version}/transitions', response_model=DataSourceRead
)
async def transition_datasource(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )
