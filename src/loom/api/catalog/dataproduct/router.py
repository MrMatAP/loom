import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, require_scopes
from loom.api.catalog.router_factory import build_versioned_router
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.schemas.dataproduct import DataProductLineageRead, DataProductRead

from .repository import DataProductRepository
from .schemas import DataProductCreateRequest, DataProductLineageCreateRequest
from .service import DataProductService


def _service_factory(session: AsyncSession) -> DataProductService:
    return DataProductService(DataProductRepository(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/dataproducts',
    tag='dataproducts',
    scope_name='dataproduct',
    read_model=DataProductRead,
    create_request_model=DataProductCreateRequest,
    service_factory=_service_factory,
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
