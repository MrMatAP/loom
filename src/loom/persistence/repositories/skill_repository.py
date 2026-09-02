import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.domain.enums import GraphNodeType, Layer, LifecycleState
from loom.domain.errors import NotFoundError
from loom.domain.skill import Skill as DomainSkill
from loom.domain.skill import SkillGraphEdge as DomainEdge
from loom.domain.skill import SkillGraphNode as DomainNode
from loom.persistence.agent import Agent as AgentRow
from loom.persistence.db import flush_or_raise
from loom.persistence.mappers import skill_mapper
from loom.persistence.skill import Skill as SkillRow
from loom.persistence.skill import SkillGraphEdge as EdgeRow
from loom.persistence.skill import SkillGraphNode as NodeRow
from loom.persistence.tool import Tool as ToolRow


class SkillRepository:
    """The persistence gateway for the Skill Aggregate -- and only the
    Skill Aggregate: `SkillGraphNode`/`SkillGraphEdge` have no repository
    of their own (CONTEXT.md's "Aggregate (root)" / "Repository" entries).
    Owns every query that touches the `skill`/`skill_graph_node`/
    `skill_graph_edge` tables, and is the only thing in this codebase
    allowed to translate between a row and the `domain.skill` Aggregate
    (via `skill_mapper`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- reads -------------------------------------------------------

    async def _row(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> SkillRow | None:
        return await self._session.scalar(
            sa.select(SkillRow).where(
                SkillRow.tenant_id == tenant_id,
                SkillRow.entity_id == entity_id,
                SkillRow.is_current.is_(True),
            )
        )

    async def _row_at_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> SkillRow | None:
        return await self._session.scalar(
            sa.select(SkillRow).where(
                SkillRow.tenant_id == tenant_id,
                SkillRow.entity_id == entity_id,
                SkillRow.version == version,
            )
        )

    def _hydrate_summary(self, row: SkillRow) -> DomainSkill:
        """No node/edge queries -- for read paths that never look at the
        graph (list/get endpoints return `SkillRead`, which has no
        nodes/edges field; `create_new_version`/`transition` only touch
        scalar fields)."""
        return skill_mapper.to_domain(row, node_rows=[], edge_rows=[], resolved_layers={})

    async def _hydrate_with_graph(self, row: SkillRow) -> DomainSkill:
        node_rows = await self.list_node_rows(row.id)
        edge_rows = await self.list_edge_rows(row.id)
        resolved_layers = {
            node_row.id: await self.resolve_node_layer(
                row.tenant_id,
                node_type=node_row.node_type,
                agent_id=node_row.agent_id,
                skill_ref_id=node_row.skill_ref_id,
                tool_id=node_row.tool_id,
            )
            for node_row in node_rows
        }
        return skill_mapper.to_domain(
            row, node_rows=node_rows, edge_rows=edge_rows, resolved_layers=resolved_layers
        )

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DomainSkill | None:
        row = await self._row(tenant_id, entity_id)
        return None if row is None else self._hydrate_summary(row)

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DomainSkill | None:
        row = await self._row_at_version(tenant_id, entity_id, version)
        return None if row is None else self._hydrate_summary(row)

    async def get_version_with_graph(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DomainSkill | None:
        """Like `get_version`, but with `nodes`/`edges` fully populated --
        only `add_edge` needs this (its layer-descent/cycle checks read
        `self.nodes`/`self.edges` on the Aggregate)."""
        row = await self._row_at_version(tenant_id, entity_id, version)
        return None if row is None else await self._hydrate_with_graph(row)

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DomainSkill]:
        rows = await self._session.scalars(
            sa.select(SkillRow)
            .where(SkillRow.tenant_id == tenant_id, SkillRow.entity_id == entity_id)
            .order_by(SkillRow.version)
        )
        return [self._hydrate_summary(row) for row in rows]

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DomainSkill], int]:
        stmt = sa.select(SkillRow).where(
            SkillRow.tenant_id == tenant_id, SkillRow.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(SkillRow.lifecycle_state == lifecycle_state)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(SkillRow.name).limit(limit).offset(offset)
        )
        return [self._hydrate_summary(row) for row in rows], total or 0

    # -- writes: the Skill row itself ---------------------------------

    async def add(self, skill: DomainSkill) -> DomainSkill:
        """Persist a brand-new version row (`create`/`new_version`).
        Never persists `skill.nodes`/`skill.edges` -- a new version always
        starts with an empty graph (see `domain.skill.Skill.new_version`),
        so there's never anything there to add at this point."""
        row = skill_mapper.to_row(skill)
        self._session.add(row)
        await flush_or_raise(self._session)
        return self._hydrate_summary(row)

    async def save(self, skill: DomainSkill) -> DomainSkill:
        """Persist mutations to an *existing* row: `is_current` bookkeeping
        (new_version's prior-row flip), a lifecycle transition, or a
        descriptive-field `update()`. Re-fetches by `id` rather than
        `session.add()`-ing a fresh row with the same PK, since `skill` is
        a detached domain object, not the ORM instance already in this
        session's identity map."""
        row = await self._session.get(SkillRow, skill.id)
        if row is None:
            raise NotFoundError(f'Skill row {skill.id} not found')
        row.is_current = skill.is_current
        row.lifecycle_state = skill.lifecycle_state
        row.approved_by_id = skill.approved_by_id
        row.approved_at = skill.approved_at
        row.name = skill.name
        row.description = skill.description
        row.maturity = skill.maturity
        row.classification = skill.classification
        await flush_or_raise(self._session)
        return self._hydrate_summary(row)

    # -- writes: nodes/edges -------------------------------------------

    async def add_node(self, skill_row_id: uuid.UUID, node: DomainNode) -> DomainNode:
        row = skill_mapper.node_to_row(node, skill_row_id=skill_row_id)
        self._session.add(row)
        await flush_or_raise(self._session)
        return node

    async def add_edge(self, skill_row_id: uuid.UUID, edge: DomainEdge) -> DomainEdge:
        row = skill_mapper.edge_to_row(edge, skill_row_id=skill_row_id)
        self._session.add(row)
        await flush_or_raise(self._session)
        return edge

    async def list_node_rows(self, skill_row_id: uuid.UUID) -> list[NodeRow]:
        result = await self._session.scalars(
            sa.select(NodeRow).where(NodeRow.skill_id == skill_row_id)
        )
        return list(result)

    async def list_edge_rows(self, skill_row_id: uuid.UUID) -> list[EdgeRow]:
        result = await self._session.scalars(
            sa.select(EdgeRow).where(EdgeRow.skill_id == skill_row_id)
        )
        return list(result)

    async def list_nodes(self, skill_row_id: uuid.UUID) -> list[DomainNode]:
        rows = await self.list_node_rows(skill_row_id)
        if not rows:
            return []
        skill_row = await self._session.get(SkillRow, skill_row_id)
        nodes = []
        for row in rows:
            resolved_layer = await self.resolve_node_layer(
                skill_row.tenant_id,
                node_type=row.node_type,
                agent_id=row.agent_id,
                skill_ref_id=row.skill_ref_id,
                tool_id=row.tool_id,
            )
            nodes.append(skill_mapper.node_to_domain(row, resolved_layer=resolved_layer))
        return nodes

    async def list_edges(self, skill_row_id: uuid.UUID) -> list[DomainEdge]:
        rows = await self.list_edge_rows(skill_row_id)
        return [skill_mapper.edge_to_domain(row) for row in rows]

    # -- opaque-leaf layer resolution ------------------------------------

    async def resolve_node_layer(
        self,
        tenant_id: uuid.UUID,
        *,
        node_type: GraphNodeType,
        agent_id: uuid.UUID | None,
        skill_ref_id: uuid.UUID | None,
        tool_id: uuid.UUID | None,
    ) -> Layer | None:
        """The referenced entity's own already-stored `layer` -- the
        *opaque leaf* value (CONTEXT.md) layer-descent checking uses,
        without the Skill Aggregate ever reaching into that entity's own
        internals. `None` for a Tool reference: Tool is never layered."""
        if node_type == GraphNodeType.AGENT:
            return await self._layer_of(AgentRow, agent_id, tenant_id)
        if node_type == GraphNodeType.SKILL:
            return await self._layer_of(SkillRow, skill_ref_id, tenant_id)
        # TOOL: unlayered, but still must exist in-tenant.
        found = await self._session.scalar(
            sa.select(ToolRow.id).where(
                ToolRow.id == tool_id, ToolRow.tenant_id == tenant_id
            )
        )
        if found is None:
            raise NotFoundError(f'Tool {tool_id} not found')
        return None

    async def _layer_of(
        self, model: type, referenced_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Layer:
        layer = await self._session.scalar(
            sa.select(model.layer).where(
                model.id == referenced_id, model.tenant_id == tenant_id
            )
        )
        if layer is None:
            raise NotFoundError(f'{model.__name__} {referenced_id} not found')
        return layer
