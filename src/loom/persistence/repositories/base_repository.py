"""Shared async persistence access for any AggregateRoot-backed resource --
get_current/get_version/list_versions/list_current/add/save, generalized
over the row type and the domain<->row Mapper functions. One instance per
Aggregate root, never for a sub-resource (CONTEXT.md's "Repository" entry).

`domain.skill.Skill` doesn't use this: its mapper needs extra
node/edge/resolved-layer arguments no other entity has, so its Repository
stays bespoke (`skill_repository.py`). Every other Aggregate here is
uniform enough that this one class covers all of them -- their concrete
Repository subclasses (`capability_repository.py` etc.) exist only to
carry their own sub-resource methods (add_realization, add_data_binding,
add_lineage, ...), never to override the versioned CRUD below.
"""

import uuid
from collections.abc import Callable
from typing import Generic, TypeVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.domain.enums import LifecycleState
from loom.domain.errors import NotFoundError
from loom.persistence.db import flush_or_raise

TDomain = TypeVar('TDomain')
TRow = TypeVar('TRow')

_MUTABLE_FIELDS = (
    'is_current',
    'lifecycle_state',
    'approved_by_id',
    'approved_at',
    'name',
    'description',
    'maturity',
    'classification',
)


class VersionedRepository(Generic[TDomain, TRow]):
    def __init__(
        self,
        session: AsyncSession,
        *,
        row_cls: type[TRow],
        to_domain: Callable[[TRow], TDomain],
        to_row: Callable[[TDomain], TRow],
    ) -> None:
        self._session = session
        self._row_cls = row_cls
        self._to_domain = to_domain
        self._to_row = to_row

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> TDomain | None:
        row = await self._session.scalar(
            sa.select(self._row_cls).where(
                self._row_cls.tenant_id == tenant_id,
                self._row_cls.entity_id == entity_id,
                self._row_cls.is_current.is_(True),
            )
        )
        return None if row is None else self._to_domain(row)

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> TDomain | None:
        row = await self._session.scalar(
            sa.select(self._row_cls).where(
                self._row_cls.tenant_id == tenant_id,
                self._row_cls.entity_id == entity_id,
                self._row_cls.version == version,
            )
        )
        return None if row is None else self._to_domain(row)

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[TDomain]:
        rows = await self._session.scalars(
            sa.select(self._row_cls)
            .where(
                self._row_cls.tenant_id == tenant_id,
                self._row_cls.entity_id == entity_id,
            )
            .order_by(self._row_cls.version)
        )
        return [self._to_domain(row) for row in rows]

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TDomain], int]:
        stmt = sa.select(self._row_cls).where(
            self._row_cls.tenant_id == tenant_id, self._row_cls.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(self._row_cls.lifecycle_state == lifecycle_state)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(self._row_cls.name).limit(limit).offset(offset)
        )
        return [self._to_domain(row) for row in rows], total or 0

    async def add(self, entity: TDomain) -> TDomain:
        row = self._to_row(entity)
        self._session.add(row)
        await flush_or_raise(self._session)
        return self._to_domain(row)

    async def save(self, entity: TDomain) -> TDomain:
        """Persist mutations to an *existing* row: `is_current` bookkeeping
        (new_version's prior-row flip), a lifecycle transition, or a
        descriptive-field `update()`. Re-fetches by `id` rather than
        `session.add()`-ing a fresh row with the same PK, since `entity`
        is a detached domain object, not the ORM instance already in this
        session's identity map."""
        row = await self._session.get(self._row_cls, entity.id)
        if row is None:
            raise NotFoundError(f'{self._row_cls.__name__} row {entity.id} not found')
        for field in _MUTABLE_FIELDS:
            setattr(row, field, getattr(entity, field))
        await flush_or_raise(self._session)
        return self._to_domain(row)
