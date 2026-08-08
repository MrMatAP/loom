import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_session, require_scopes
from loom.api.catalog.pagination import Page, PaginationParams
from loom.model.schemas.tenant import PrincipalCreate, PrincipalRead, PrincipalUpdate

from .repository import PrincipalRepository
from .service import PrincipalService

router = APIRouter(prefix='/principals', tags=['principals'])


def _service(session: AsyncSession = Depends(get_session)) -> PrincipalService:
    return PrincipalService(PrincipalRepository(session))


@router.post('', response_model=PrincipalRead, status_code=201)
async def create_principal(
    body: PrincipalCreate,
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:write')),
):
    return await service.create(body)


@router.get('', response_model=Page[PrincipalRead])
async def list_principals(
    tenant_id: uuid.UUID = Query(...),  # noqa: B008
    pagination: PaginationParams = Depends(),
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:read')),
):
    items, total = await service.list_by_tenant(
        tenant_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get('/{principal_id}', response_model=PrincipalRead)
async def get_principal(
    principal_id: uuid.UUID,
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:read')),
):
    return await service.get(principal_id)


@router.patch('/{principal_id}', response_model=PrincipalRead)
async def update_principal(
    principal_id: uuid.UUID,
    body: PrincipalUpdate,
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:write')),
):
    return await service.update(principal_id, body)
