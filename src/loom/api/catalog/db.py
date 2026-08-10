import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import DomainValidationError, EntityNotFoundError


async def flush_or_raise(session: AsyncSession) -> None:
    """Flush the session, translating IntegrityError into a domain error."""
    try:
        await session.flush()
    except sa.exc.IntegrityError as exc:
        await session.rollback()
        raise DomainValidationError(str(exc.orig)) from exc


async def assert_same_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
) -> None:
    """Raise unless `entity_id` resolves to a row of `model` inside `tenant_id`."""
    found = await session.scalar(
        sa.select(model.id).where(model.id == entity_id, model.tenant_id == tenant_id)
    )
    if found is None:
        raise EntityNotFoundError(f'{model.__name__} {entity_id} not found')


async def assert_current_version_in_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
) -> None:
    """Raise unless `entity_id` resolves to `model`'s *current* version row
    inside `tenant_id`. For floating bindings (e.g. Agent.model_binding_id)
    that reference a VersionedEntityMixin's `entity_id` rather than a
    specific version row's `id`, which isn't a candidate key and so can't
    back a DB-level FK."""
    found = await session.scalar(
        sa.select(model.id).where(
            model.entity_id == entity_id,
            model.tenant_id == tenant_id,
            model.is_current.is_(True),
        )
    )
    if found is None:
        raise EntityNotFoundError(f'{model.__name__} {entity_id} not found')
