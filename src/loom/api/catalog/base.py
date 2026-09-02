import datetime
import uuid
from typing import Generic, TypeVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import (
    assert_current_version_in_tenant,
    assert_same_tenant,
    flush_or_raise,
)
from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.domain.enums import LifecycleState
from loom.persistence.base import VersionedEntityMixin

T = TypeVar('T', bound=VersionedEntityMixin)


class BaseRepository(Generic[T]):
    """Shared async persistence logic for any VersionedEntityMixin model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def model(self) -> type[T]:
        raise NotImplementedError

    async def assert_same_tenant(
        self, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
    ) -> None:
        """Raise unless the referenced entity resolves inside this tenant."""
        await assert_same_tenant(self._session, tenant_id, model, entity_id)

    async def assert_current_version_in_tenant(
        self, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
    ) -> None:
        """Raise unless `entity_id` resolves to `model`'s current version
        inside this tenant. For floating bindings keyed by entity_id."""
        await assert_current_version_in_tenant(
            self._session, tenant_id, model, entity_id
        )

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> T | None:
        return await self._session.scalar(
            sa.select(self.model).where(
                self.model.tenant_id == tenant_id,
                self.model.entity_id == entity_id,
                self.model.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> T | None:
        return await self._session.scalar(
            sa.select(self.model).where(
                self.model.tenant_id == tenant_id,
                self.model.entity_id == entity_id,
                self.model.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[T]:
        result = await self._session.scalars(
            sa.select(self.model)
            .where(self.model.tenant_id == tenant_id, self.model.entity_id == entity_id)
            .order_by(self.model.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[T], int]:
        stmt = sa.select(self.model).where(
            self.model.tenant_id == tenant_id, self.model.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(self.model.lifecycle_state == lifecycle_state)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(self.model.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, entity: T) -> T:
        self._session.add(entity)
        await flush_or_raise(self._session)
        return entity

    async def save(self, entity: T) -> T:
        await flush_or_raise(self._session)
        return entity


class BaseService(Generic[T]):
    """Shared read/transition use-cases for any VersionedEntityMixin
    aggregate. `create`/`create_new_version` stay resource-specific --
    the fields they populate are exactly what differs between resources,
    so factoring them here would trade real duplication for an
    indirection that hides each resource's own shape."""

    def __init__(self, repository: BaseRepository[T]) -> None:
        self._repository = repository

    @property
    def label(self) -> str:
        """Human-readable entity name for error messages, e.g. 'Capability'."""
        raise NotImplementedError

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> T:
        entity = await self._repository.get_current(tenant_id, entity_id)
        if entity is None:
            raise EntityNotFoundError(f'{self.label} {entity_id} not found')
        return entity

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> T:
        entity = await self._repository.get_version(tenant_id, entity_id, version)
        if entity is None:
            detail = f'{self.label} {entity_id} version {version} not found'
            raise EntityNotFoundError(detail)
        return entity

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[T]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'{self.label} {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[T], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            limit=limit,
            offset=offset,
        )

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> T:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = (
                f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            )
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)
