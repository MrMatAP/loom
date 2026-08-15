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
from loom.model.schemas.dataproduct import DataProductLineageRead, DataProductRead

from .repository import DataProductRepository
from .schemas import DataProductCreateRequest, DataProductLineageCreateRequest
from .service import DataProductService

router = APIRouter(prefix='/tenants/{tenant_id}/dataproducts', tags=['dataproducts'])


def _service(session: AsyncSession = Depends(get_session)) -> DataProductService:
    return DataProductService(DataProductRepository(session))


@router.post('', response_model=DataProductRead, status_code=201)
async def create_dataproduct(
    body: DataProductCreateRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[DataProductRead])
async def list_dataproducts(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
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


@router.get('/{entity_id}', response_model=DataProductRead)
async def get_dataproduct(
    entity_id: uuid.UUID,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[DataProductRead])
async def list_dataproduct_versions(
    entity_id: uuid.UUID,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=DataProductRead)
async def get_dataproduct_version(
    entity_id: uuid.UUID,
    version: int,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=DataProductRead, status_code=201)
async def create_dataproduct_version(
    entity_id: uuid.UUID,
    body: DataProductCreateRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post(
    '/{entity_id}/versions/{version}/transitions', response_model=DataProductRead
)
async def transition_dataproduct(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )


@router.post(
    '/{entity_id}/versions/{version}/lineage',
    response_model=DataProductLineageRead,
    status_code=201,
)
async def add_dataproduct_lineage(
    entity_id: uuid.UUID,
    version: int,
    body: DataProductLineageCreateRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:write')),
):
    return await service.add_lineage(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/lineage',
    response_model=list[DataProductLineageRead],
)
async def list_dataproduct_lineage(
    entity_id: uuid.UUID,
    version: int,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.list_lineage(principal.tenant_id, entity_id, version)
