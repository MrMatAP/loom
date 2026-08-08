import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import DomainValidationError


async def flush_or_raise(session: AsyncSession) -> None:
    """Flush the session, translating IntegrityError into a domain error."""
    try:
        await session.flush()
    except sa.exc.IntegrityError as exc:
        await session.rollback()
        raise DomainValidationError(str(exc.orig)) from exc
