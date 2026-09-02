import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.domain.errors import ConstraintViolation


async def flush_or_raise(session: AsyncSession) -> None:
    """Flush the session, translating IntegrityError into a domain error.
    A `loom.persistence`-local twin of `api.catalog.db.flush_or_raise`,
    raising `loom.domain.errors.ConstraintViolation` instead of an
    api.catalog-owned exception -- persistence depends on domain, never on
    the API layer (see CONTEXT.md)."""
    try:
        await session.flush()
    except sa.exc.IntegrityError as exc:
        await session.rollback()
        raise ConstraintViolation(str(exc.orig)) from exc
