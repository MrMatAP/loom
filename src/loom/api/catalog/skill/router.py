"""Skill's basic 7 routes now go through the same `build_versioned_router`
factory every other resource uses -- it only ever needed a
`Callable[[AsyncSession], <application service>]`, which
`SkillApplicationService(UnitOfWork(session))` satisfies exactly like the
other six. Only the graph sub-routes (nodes/edges, keyed on
`(entity_id, version)`) stay hand-written here, same shape as Capability's
realizations / Tool's data-bindings / DataProduct's lineage."""

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, require_scopes
from loom.api.catalog.router_factory import build_versioned_router
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.skill import SkillGraphEdgeRead, SkillGraphNodeRead, SkillRead

from .application_service import SkillApplicationService
from .schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)


def _service_factory(session: AsyncSession) -> SkillApplicationService:
    return SkillApplicationService(UnitOfWork(session))


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
    service: SkillApplicationService = Depends(_service),
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
    service: SkillApplicationService = Depends(_service),
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
    service: SkillApplicationService = Depends(_service),
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
    service: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.list_edges(principal.tenant_id, entity_id, version)
