"""Owns the transaction boundary for one logical operation and exposes one
Repository per Aggregate it touches -- see CONTEXT.md's "Unit of Work"
entry. Only `skills` today (the pilot); a future Aggregate's Repository
gets its own lazily-constructed property here, not a second UnitOfWork
class.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from loom.persistence.repositories.skill_repository import SkillRepository


class UnitOfWork:
    """`async with UnitOfWork(session) as uow: uow.skills.add(skill)` --
    commits on clean exit, rolls back on an exception (including a raised
    domain error), same transaction-per-operation shape the FastAPI
    `get_session` dependency already gives every request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.skills = SkillRepository(session)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()


@asynccontextmanager
async def unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[UnitOfWork]:
    """Convenience for call sites that own their own session_factory
    (scripts, tests) rather than getting a session handed to them by
    FastAPI's per-request dependency (see `application_service.py`'s
    `Depends(get_session)` for that path -- it constructs `UnitOfWork`
    directly around the request-scoped session instead of this)."""
    async with session_factory() as session, UnitOfWork(session) as uow:
        yield uow
