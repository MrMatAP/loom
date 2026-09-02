import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, require_scopes
from loom.api.catalog.router_factory import build_versioned_router
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.tool import ToolDataBindingRead, ToolRead

from .application_service import ToolApplicationService
from .schemas import ToolCreateRequest, ToolDataBindingCreateRequest


def _service_factory(session: AsyncSession) -> ToolApplicationService:
    return ToolApplicationService(UnitOfWork(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/tools',
    tag='tools',
    scope_name='tool',
    read_model=ToolRead,
    create_request_model=ToolCreateRequest,
    service_factory=_service_factory,
)


@router.post(
    '/{entity_id}/versions/{version}/data-bindings',
    response_model=ToolDataBindingRead,
    status_code=201,
)
async def add_tool_data_binding(
    entity_id: uuid.UUID,
    version: int,
    body: ToolDataBindingCreateRequest,
    service: ToolApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:write')),
):
    return await service.add_data_binding(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/data-bindings',
    response_model=list[ToolDataBindingRead],
)
async def list_tool_data_bindings(
    entity_id: uuid.UUID,
    version: int,
    service: ToolApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.list_data_bindings(principal.tenant_id, entity_id, version)
