"""Shared read/transition use-cases for any AggregateRoot-backed resource --
the Application Service equivalent of `api.catalog.base.BaseService`
(CONTEXT.md's "Application Service" entry). `create`/`create_new_version`
stay resource-specific on each subclass, same reasoning `BaseService`
already documented: the fields they populate are exactly what differs
between resources, so factoring them here would trade real duplication for
an indirection that hides each resource's own shape.

`skill/application_service.py` doesn't use this: Skill's graph sub-resource
methods (add_node/add_edge/...) don't fit this shape, so it stays bespoke.
"""

import datetime
import uuid
from typing import Generic, TypeVar

from loom.domain.enums import LifecycleState
from loom.domain.errors import IllegalTransitionError, NotFoundError

TDomain = TypeVar('TDomain')
TRead = TypeVar('TRead')


class VersionedApplicationService(Generic[TDomain, TRead]):
    label: str = ''
    read_cls: type[TRead]

    def __init__(self, uow, *, repository_attr: str) -> None:
        self._uow = uow
        self._repository_attr = repository_attr

    @property
    def _repo(self):
        return getattr(self._uow, self._repository_attr)

    def _read(self, entity: TDomain) -> TRead:
        return self.read_cls.model_validate(entity, from_attributes=True)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> TRead:
        return self._read(await self._get_current_or_raise(tenant_id, entity_id))

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> TRead:
        return self._read(
            await self._get_version_or_raise(tenant_id, entity_id, version)
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[TRead]:
        versions = await self._repo.list_versions(tenant_id, entity_id)
        if not versions:
            raise NotFoundError(f'{self.label} {entity_id} not found')
        return [self._read(v) for v in versions]

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TRead], int]:
        items, total = await self._repo.list_current(
            tenant_id, lifecycle_state=lifecycle_state, limit=limit, offset=offset
        )
        return [self._read(i) for i in items], total

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> TRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        if current.version != version:
            raise IllegalTransitionError(
                f'Version {version} is not the current version of {entity_id}'
            )
        current.transition(
            to_state, actor_id=actor_id, now=datetime.datetime.now(datetime.UTC)
        )
        saved = await self._repo.save(current)
        return self._read(saved)

    # -- internal: the domain object, not yet converted to a Read schema --
    # (used by this class's own transition() above, and by every
    # subclass's create_new_version()).

    async def _get_current_or_raise(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> TDomain:
        entity = await self._repo.get_current(tenant_id, entity_id)
        if entity is None:
            raise NotFoundError(f'{self.label} {entity_id} not found')
        return entity

    async def _get_version_or_raise(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> TDomain:
        entity = await self._repo.get_version(tenant_id, entity_id, version)
        if entity is None:
            raise NotFoundError(f'{self.label} {entity_id} version {version} not found')
        return entity
