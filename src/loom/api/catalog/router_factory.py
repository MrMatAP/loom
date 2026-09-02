import uuid
from collections.abc import Callable

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
from loom.schemas.lifecycle import TransitionRequest

from .base import BaseService


def build_versioned_router(
    *,
    prefix: str,
    tag: str,
    scope_name: str,
    read_model: type,
    create_request_model: type,
    service_factory: Callable[[AsyncSession], BaseService],
) -> tuple[APIRouter, Callable[..., BaseService]]:
    """Register the 7 routes every `VersionedEntityMixin`-backed resource
    exposes identically: create, list, get, list versions, get version,
    create new version, transition. `scope_name` drives the
    `catalog:{scope_name}:{action}` RBAC scopes.

    Returns `(router, service)` rather than just the router -- entities
    with their own sub-resource routes (capability realizations, tool
    data-bindings, dataproduct lineage, skill graph edges) attach those
    directly to the returned router using the same `service` dependency,
    typed to their own Service subclass by whatever `service_factory`
    returns.
    """
    router = APIRouter(prefix=prefix, tags=[tag])

    def service(session: AsyncSession = Depends(get_session)):
        return service_factory(session)

    @router.post('', response_model=read_model, status_code=201)
    async def create(
        body: create_request_model,
        svc=Depends(service),
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        _scopes: None = Depends(require_scopes(f'catalog:{scope_name}:write')),
    ):
        return await svc.create(
            tenant_id=principal.tenant_id,
            created_by_id=principal.principal_id,
            data=body,
        )

    @router.get('', response_model=Page[read_model])
    async def list_current(
        pagination: PaginationParams = Depends(),
        lifecycle_state: LifecycleState | None = None,
        svc=Depends(service),
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        _scopes: None = Depends(require_scopes(f'catalog:{scope_name}:read')),
    ):
        items, total = await svc.list_current(
            principal.tenant_id,
            lifecycle_state=lifecycle_state,
            limit=pagination.limit,
            offset=pagination.offset,
        )
        return Page(
            items=items, total=total, limit=pagination.limit, offset=pagination.offset
        )

    @router.get('/{entity_id}', response_model=read_model)
    async def get_current(
        entity_id: uuid.UUID,
        svc=Depends(service),
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        _scopes: None = Depends(require_scopes(f'catalog:{scope_name}:read')),
    ):
        return await svc.get_current(principal.tenant_id, entity_id)

    @router.get('/{entity_id}/versions', response_model=list[read_model])
    async def list_versions(
        entity_id: uuid.UUID,
        svc=Depends(service),
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        _scopes: None = Depends(require_scopes(f'catalog:{scope_name}:read')),
    ):
        return await svc.list_versions(principal.tenant_id, entity_id)

    @router.get('/{entity_id}/versions/{version}', response_model=read_model)
    async def get_version(
        entity_id: uuid.UUID,
        version: int,
        svc=Depends(service),
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        _scopes: None = Depends(require_scopes(f'catalog:{scope_name}:read')),
    ):
        return await svc.get_version(principal.tenant_id, entity_id, version)

    @router.post('/{entity_id}/versions', response_model=read_model, status_code=201)
    async def create_version(
        entity_id: uuid.UUID,
        body: create_request_model,
        svc=Depends(service),
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        _scopes: None = Depends(require_scopes(f'catalog:{scope_name}:write')),
    ):
        return await svc.create_new_version(
            tenant_id=principal.tenant_id,
            created_by_id=principal.principal_id,
            entity_id=entity_id,
            data=body,
        )

    @router.post(
        '/{entity_id}/versions/{version}/transitions', response_model=read_model
    )
    async def transition(
        entity_id: uuid.UUID,
        version: int,
        body: TransitionRequest,
        svc=Depends(service),
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        _scopes: None = Depends(require_scopes(f'catalog:{scope_name}:transition')),
    ):
        return await svc.transition(
            tenant_id=principal.tenant_id,
            entity_id=entity_id,
            version=version,
            to_state=body.to_state,
            actor_id=principal.principal_id,
        )

    return router, service
