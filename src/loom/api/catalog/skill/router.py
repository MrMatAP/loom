"""Skill's routes are hand-written here rather than via
`router_factory.build_versioned_router`: that factory calls straight into
a `BaseService[T]` wrapping an ORM row, which is exactly the shape Skill
moved off of (see docs/adr/0001-ddd-separation-for-catalog-domain.md).
The other six resources are unaffected and still use the factory."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import (
    get_current_principal,
    get_session,
    require_scopes,
)
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.domain.enums import LifecycleState
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.lifecycle import TransitionRequest
from loom.schemas.skill import SkillGraphEdgeRead, SkillGraphNodeRead, SkillRead

from .application_service import SkillApplicationService
from .schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)

router = APIRouter(prefix='/tenants/{tenant_id}/skills', tags=['skills'])


def _uow(session: AsyncSession = Depends(get_session)) -> UnitOfWork:
    return UnitOfWork(session)


def _service(uow: UnitOfWork = Depends(_uow)) -> SkillApplicationService:
    return SkillApplicationService(uow)


@router.post('', response_model=SkillRead, status_code=201)
async def create(
    body: SkillCreateRequest,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await svc.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[SkillRead])
async def list_current(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    items, total = await svc.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{entity_id}', response_model=SkillRead)
async def get_current(
    entity_id: uuid.UUID,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await svc.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[SkillRead])
async def list_versions(
    entity_id: uuid.UUID,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await svc.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=SkillRead)
async def get_version(
    entity_id: uuid.UUID,
    version: int,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await svc.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=SkillRead, status_code=201)
async def create_version(
    entity_id: uuid.UUID,
    body: SkillCreateRequest,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await svc.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=SkillRead)
async def transition(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:transition')),
):
    return await svc.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
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
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await svc.add_node(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/nodes', response_model=list[SkillGraphNodeRead]
)
async def list_skill_nodes(
    entity_id: uuid.UUID,
    version: int,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await svc.list_nodes(principal.tenant_id, entity_id, version)


@router.post(
    '/{entity_id}/versions/{version}/edges',
    response_model=SkillGraphEdgeRead,
    status_code=201,
)
async def add_skill_edge(
    entity_id: uuid.UUID,
    version: int,
    body: SkillGraphEdgeCreateRequest,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await svc.add_edge(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/edges', response_model=list[SkillGraphEdgeRead]
)
async def list_skill_edges(
    entity_id: uuid.UUID,
    version: int,
    svc: SkillApplicationService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await svc.list_edges(principal.tenant_id, entity_id, version)
