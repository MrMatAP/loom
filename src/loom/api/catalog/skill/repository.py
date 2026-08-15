import uuid

import sqlalchemy as sa

from loom.api.catalog.base import BaseRepository
from loom.api.catalog.db import flush_or_raise
from loom.model.skill import Skill, SkillGraphEdge, SkillGraphNode


class SkillRepository(BaseRepository[Skill]):
    """Async persistence access for Skill, scoped by tenant."""

    @property
    def model(self) -> type[Skill]:
        return Skill

    async def add_node(self, node: SkillGraphNode) -> SkillGraphNode:
        self._session.add(node)
        await flush_or_raise(self._session)
        return node

    async def list_nodes(self, skill_version_id: uuid.UUID) -> list[SkillGraphNode]:
        result = await self._session.scalars(
            sa.select(SkillGraphNode).where(SkillGraphNode.skill_id == skill_version_id)
        )
        return list(result)

    async def get_node(
        self, skill_version_id: uuid.UUID, node_id: uuid.UUID
    ) -> SkillGraphNode | None:
        return await self._session.scalar(
            sa.select(SkillGraphNode).where(
                SkillGraphNode.skill_id == skill_version_id,
                SkillGraphNode.id == node_id,
            )
        )

    async def add_edge(self, edge: SkillGraphEdge) -> SkillGraphEdge:
        self._session.add(edge)
        await flush_or_raise(self._session)
        return edge

    async def list_edges(self, skill_version_id: uuid.UUID) -> list[SkillGraphEdge]:
        result = await self._session.scalars(
            sa.select(SkillGraphEdge).where(SkillGraphEdge.skill_id == skill_version_id)
        )
        return list(result)
