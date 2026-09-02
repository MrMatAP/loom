import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.domain.capability import CapabilityRealization as DomainRealization
from loom.persistence.agent import Agent as AgentRow
from loom.persistence.capability import Capability as CapabilityRow
from loom.persistence.capability import CapabilityRealization as RealizationRow
from loom.persistence.db import assert_same_tenant, flush_or_raise
from loom.persistence.mappers import capability_mapper
from loom.persistence.repositories.base_repository import VersionedRepository
from loom.persistence.skill import Skill as SkillRow
from loom.persistence.tool import Tool as ToolRow


class CapabilityRepository(VersionedRepository):
    """The persistence gateway for the Capability Aggregate -- and only
    the Capability Aggregate: `CapabilityRealization` has no repository of
    its own (CONTEXT.md's "Aggregate (root)" entry)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            row_cls=CapabilityRow,
            to_domain=capability_mapper.to_domain,
            to_row=capability_mapper.to_row,
        )

    async def assert_realizer_in_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        realizing_agent_id: uuid.UUID | None,
        realizing_skill_id: uuid.UUID | None,
        realizing_tool_id: uuid.UUID | None,
    ) -> None:
        for row_cls, referenced_id in (
            (AgentRow, realizing_agent_id),
            (SkillRow, realizing_skill_id),
            (ToolRow, realizing_tool_id),
        ):
            if referenced_id is not None:
                await assert_same_tenant(self._session, tenant_id, row_cls, referenced_id)

    async def add_realization(
        self, capability_row_id: uuid.UUID, realization: DomainRealization
    ) -> DomainRealization:
        row = capability_mapper.realization_to_row(
            realization, capability_row_id=capability_row_id
        )
        self._session.add(row)
        await flush_or_raise(self._session)
        return realization

    async def list_realizations(
        self, capability_row_id: uuid.UUID
    ) -> list[DomainRealization]:
        rows = await self._session.scalars(
            sa.select(RealizationRow).where(
                RealizationRow.capability_id == capability_row_id
            )
        )
        return [capability_mapper.realization_to_domain(row) for row in rows]
