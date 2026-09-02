import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_session, require_scopes
from loom.api.catalog.pagination import Page, PaginationParams
from loom.schemas.environment import (
    EnvironmentCreate,
    EnvironmentRead,
    EnvironmentUpdate,
)

from .repository import EnvironmentRepository
from .service import EnvironmentService

router = APIRouter(prefix='/tenants/{tenant_id}/environments', tags=['environments'])


def _service(session: AsyncSession = Depends(get_session)) -> EnvironmentService:
    return EnvironmentService(EnvironmentRepository(session))


@router.post('', response_model=EnvironmentRead, status_code=201)
async def create_environment(
    tenant_id: uuid.UUID,
    body: EnvironmentCreate,
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:write')),
):
    return await service.create(tenant_id, body)


@router.get('', response_model=Page[EnvironmentRead])
async def list_environments(
    tenant_id: uuid.UUID,
    pagination: PaginationParams = Depends(),
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:read')),
):
    items, total = await service.list_by_tenant(
        tenant_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get('/{environment_id}', response_model=EnvironmentRead)
async def get_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:read')),
):
    return await service.get(tenant_id, environment_id)


@router.patch('/{environment_id}', response_model=EnvironmentRead)
async def update_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    body: EnvironmentUpdate,
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:write')),
):
    return await service.update(tenant_id, environment_id, body)
