import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.audit import AuditActor, get_audit_actor, record_audit_event
from loom.api.catalog.dependencies import get_session, require_scopes
from loom.api.catalog.pagination import Page, PaginationParams
from loom.schemas.tenant import PrincipalCreate, PrincipalRead

from .repository import PrincipalRepository
from .service import PrincipalService

router = APIRouter(prefix='/tenants/{tenant_id}/principals', tags=['principals'])


def _service(session: AsyncSession = Depends(get_session)) -> PrincipalService:
    return PrincipalService(PrincipalRepository(session))


@router.post('', response_model=PrincipalRead, status_code=201)
async def create_principal(
    tenant_id: uuid.UUID,
    body: PrincipalCreate,
    session: AsyncSession = Depends(get_session),
    service: PrincipalService = Depends(_service),
    actor: AuditActor = Depends(get_audit_actor),
    _scopes: None = Depends(require_scopes('catalog:principal:write')),
):
    principal = await service.create(tenant_id, body)
    await record_audit_event(
        session,
        tenant_id=principal.tenant_id,
        actor=actor,
        action='principal.create',
        entity_type='principal',
        entity_id=principal.id,
    )
    return principal


@router.get('', response_model=Page[PrincipalRead])
async def list_principals(
    tenant_id: uuid.UUID,
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
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:read')),
):
    return await service.get(tenant_id, principal_id)
