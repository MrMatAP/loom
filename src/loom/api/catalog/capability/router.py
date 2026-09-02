import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, require_scopes
from loom.api.catalog.router_factory import build_versioned_router
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.schemas.capability import CapabilityRead, CapabilityRealizationRead

from .repository import CapabilityRepository
from .schemas import CapabilityCreateRequest, CapabilityRealizationCreateRequest
from .service import CapabilityService


def _service_factory(session: AsyncSession) -> CapabilityService:
    return CapabilityService(CapabilityRepository(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/capabilities',
    tag='capabilities',
    scope_name='capability',
    read_model=CapabilityRead,
    create_request_model=CapabilityCreateRequest,
    service_factory=_service_factory,
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
