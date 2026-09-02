import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, require_scopes
from loom.api.catalog.router_factory import build_versioned_router
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.schemas.skill import SkillGraphEdgeRead, SkillGraphNodeRead, SkillRead

from .repository import SkillRepository
from .schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)
from .service import SkillService


def _service_factory(session: AsyncSession) -> SkillService:
    return SkillService(SkillRepository(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/skills',
    tag='skills',
    scope_name='skill',
    read_model=SkillRead,
    create_request_model=SkillCreateRequest,
    service_factory=_service_factory,
)


@router.post(
    '/{entity_id}/versions/{version}/nodes',
    response_model=SkillGraphNodeRead,
    status_code=201,
)
async def add_skill_node(
    entity_id: uuid.UUID,
    version: int,
    body: SkillGraphNodeCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.add_node(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/nodes', response_model=list[SkillGraphNodeRead]
)
async def list_skill_nodes(
    entity_id: uuid.UUID,
    version: int,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.list_nodes(principal.tenant_id, entity_id, version)


@router.post(
    '/{entity_id}/versions/{version}/edges',
    response_model=SkillGraphEdgeRead,
    status_code=201,
)
async def add_skill_edge(
    entity_id: uuid.UUID,
    version: int,
    body: SkillGraphEdgeCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.add_edge(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/edges', response_model=list[SkillGraphEdgeRead]
)
async def list_skill_edges(
    entity_id: uuid.UUID,
    version: int,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.list_edges(principal.tenant_id, entity_id, version)
