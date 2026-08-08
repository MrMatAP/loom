import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import assert_same_tenant, flush_or_raise
from loom.model.enums import LifecycleState
from loom.model.skill import Skill, SkillGraphEdge, SkillGraphNode


class SkillRepository:
    """Async persistence access for Skill, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assert_same_tenant(
        self, tenant_id: uuid.UUID, model: type, entity_id: uuid.UUID
    ) -> None:
        """Raise unless the referenced entity resolves inside this tenant."""
        await assert_same_tenant(self._session, tenant_id, model, entity_id)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> Skill | None:
        return await self._session.scalar(
            sa.select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.entity_id == entity_id,
                Skill.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Skill | None:
        return await self._session.scalar(
            sa.select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.entity_id == entity_id,
                Skill.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Skill]:
        result = await self._session.scalars(
            sa.select(Skill)
            .where(Skill.tenant_id == tenant_id, Skill.entity_id == entity_id)
            .order_by(Skill.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Skill], int]:
        stmt = sa.select(Skill).where(
            Skill.tenant_id == tenant_id, Skill.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Skill.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(Skill.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Skill.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, skill: Skill) -> Skill:
        self._session.add(skill)
        await flush_or_raise(self._session)
        return skill

    async def save(self, skill: Skill) -> Skill:
        await flush_or_raise(self._session)
        return skill

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
