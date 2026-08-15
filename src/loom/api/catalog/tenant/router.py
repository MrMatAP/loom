import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.audit import (
    AuditActor,
    get_audit_actor_for_new_tenant,
    record_audit_event,
)
from loom.api.catalog.dependencies import (
    get_current_token,
    get_session,
    principals_for_external_id,
    require_scopes,
)
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import expand_claims_to_scopes
from loom.model.schemas.tenant import TenantCreate, TenantRead, TenantUpdate

from .repository import TenantRepository
from .service import TenantService

# Cap for `GET /tenants/mine` -- not a paginated listing (see its own
# docstring), just generous enough that even a platform admin in a large
# multi-tenant deployment sees every choice at login time.
_MINE_LIMIT = 1000

router = APIRouter(prefix='/tenants', tags=['tenants'])


def _service(session: AsyncSession = Depends(get_session)) -> TenantService:
    return TenantService(TenantRepository(session))


@router.post('', response_model=TenantRead, status_code=201)
async def create_tenant(
    body: TenantCreate,
    session: AsyncSession = Depends(get_session),
    service: TenantService = Depends(_service),
    actor: AuditActor = Depends(get_audit_actor_for_new_tenant),
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


@router.get('/mine', response_model=Page[TenantRead])
async def list_my_tenants(
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
    service: TenantService = Depends(_service),
):
    """Tenants available to the caller -- self-service, no `catalog:tenant:
    read`/`catalog:principal:read` scope required, since it only ever
    returns the caller's own data. Backs `loom auth login`'s tenant
    selection step, which needs this *before* a Tenant has been chosen --
    every other route's `get_current_principal` takes `tenant_id` from the
    URL path, which is exactly what hasn't been picked yet here.

    A token carrying `catalog:tenant:read` (the `catalog-platform-admin`
    bundle -- see docs/admin-guide.md's "Platform administrator" section)
    sees every Tenant, since a platform admin may not have a Principal of
    their own in any of them yet. Everyone else sees only the Tenants
    where they already have a Principal (matched by `sub`, via
    `principals_for_external_id`), never more -- this must not become a
    way to discover Tenants a caller isn't otherwise provisioned in."""
    scopes = expand_claims_to_scopes(claims)
    if 'catalog:tenant:read' in scopes:
        items, total = await service.list_all(limit=_MINE_LIMIT, offset=0)
        return Page(items=items, total=total, limit=_MINE_LIMIT, offset=0)

    sub = claims.get('sub')
    tenant_ids: set[uuid.UUID] = set()
    if isinstance(sub, str) and sub:
        principals = await principals_for_external_id(session, sub)
        tenant_ids = {p.tenant_id for p in principals}
    items = await service.list_by_ids(tenant_ids)
    return Page(items=items, total=len(items), limit=_MINE_LIMIT, offset=0)


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
