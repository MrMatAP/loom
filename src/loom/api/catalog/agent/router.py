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
from loom.model.schemas.agent import AgentRead

from .repository import AgentRepository
from .schemas import AgentCreateRequest
from .service import AgentService

router = APIRouter(prefix='/agents', tags=['agents'])


def _service(session: AsyncSession = Depends(get_session)) -> AgentService:
    return AgentService(AgentRepository(session))


@router.post('', response_model=AgentRead, status_code=201)
async def create_agent(
    body: AgentCreateRequest,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[AgentRead])
async def list_agents(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get('/{entity_id}', response_model=AgentRead)
async def get_agent(
    entity_id: uuid.UUID,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[AgentRead])
async def list_agent_versions(
    entity_id: uuid.UUID,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=AgentRead)
async def get_agent_version(
    entity_id: uuid.UUID,
    version: int,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=AgentRead, status_code=201)
async def create_agent_version(
    entity_id: uuid.UUID,
    body: AgentCreateRequest,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=AgentRead)
async def transition_agent(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )
