import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.audit import AuditActor, get_audit_actor, record_audit_event
from loom.api.catalog.dependencies import get_session, require_scopes
from loom.api.catalog.pagination import Page, PaginationParams
from loom.model.schemas.tenant import TenantCreate, TenantRead, TenantUpdate

from .repository import TenantRepository
from .service import TenantService

router = APIRouter(prefix='/tenants', tags=['tenants'])


def _service(session: AsyncSession = Depends(get_session)) -> TenantService:
    return TenantService(TenantRepository(session))


@router.post('', response_model=TenantRead, status_code=201)
async def create_tenant(
    body: TenantCreate,
    session: AsyncSession = Depends(get_session),
    service: TenantService = Depends(_service),
    actor: AuditActor = Depends(get_audit_actor),
    _scopes: None = Depends(require_scopes('catalog:tenant:write')),
):
    tenant = await service.create(body)
    await record_audit_event(
        session,
        tenant_id=tenant.id,
        actor=actor,
        action='tenant.create',
        entity_type='tenant',
        entity_id=tenant.id,
    )
    return tenant


@router.get('', response_model=Page[TenantRead])
async def list_tenants(
    pagination: PaginationParams = Depends(),
    service: TenantService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:tenant:read')),
):
    items, total = await service.list_all(
        limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get('/{tenant_id}', response_model=TenantRead)
async def get_tenant(
    tenant_id: uuid.UUID,
    service: TenantService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:tenant:read')),
):
    return await service.get(tenant_id)


@router.patch('/{tenant_id}', response_model=TenantRead)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    service: TenantService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:tenant:write')),
):
    return await service.update(tenant_id, body)
