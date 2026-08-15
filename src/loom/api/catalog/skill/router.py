import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import (
    get_current_principal,
    get_session,
    require_scopes,
)
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.skill import SkillGraphEdgeRead, SkillGraphNodeRead, SkillRead

from .repository import SkillRepository
from .schemas import (
    SkillCreateRequest,
    SkillGraphEdgeCreateRequest,
    SkillGraphNodeCreateRequest,
)
from .service import SkillService

router = APIRouter(prefix='/tenants/{tenant_id}/skills', tags=['skills'])


def _service(session: AsyncSession = Depends(get_session)) -> SkillService:
    return SkillService(SkillRepository(session))


@router.post('', response_model=SkillRead, status_code=201)
async def create_skill(
    body: SkillCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[SkillRead])
async def list_skills(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get('/{entity_id}', response_model=SkillRead)
async def get_skill(
    entity_id: uuid.UUID,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[SkillRead])
async def list_skill_versions(
    entity_id: uuid.UUID,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=SkillRead)
async def get_skill_version(
    entity_id: uuid.UUID,
    version: int,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=SkillRead, status_code=201)
async def create_skill_version(
    entity_id: uuid.UUID,
    body: SkillCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=SkillRead)
async def transition_skill(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:transition')),
):
    return await service.transition(
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
