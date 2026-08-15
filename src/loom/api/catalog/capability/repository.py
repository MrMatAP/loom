import uuid

import sqlalchemy as sa

from loom.api.catalog.base import BaseRepository
from loom.api.catalog.db import flush_or_raise
from loom.model.capability import Capability, CapabilityRealization


class CapabilityRepository(BaseRepository[Capability]):
    """Async persistence access for Capability, scoped by tenant."""

    @property
    def model(self) -> type[Capability]:
        return Capability

    async def add_realization(
        self, realization: CapabilityRealization
    ) -> CapabilityRealization:
        self._session.add(realization)
        await flush_or_raise(self._session)
        return realization

    async def list_realizations(
        self, capability_version_id: uuid.UUID
    ) -> list[CapabilityRealization]:
        result = await self._session.scalars(
            sa.select(CapabilityRealization).where(
                CapabilityRealization.capability_id == capability_version_id
            )
        )
        return list(result)
