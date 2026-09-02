"""Pure translation between `persistence.skill` rows and the `domain.skill`
Aggregate. No I/O, no session -- that's the Repository's job (see
CONTEXT.md's "Mapper" / "Repository" entries). Deliberately hand-written
rather than SQLAlchemy classical/imperative mapping, per
docs/adr/0001-ddd-separation-for-catalog-domain.md: construction here can
refuse to build a Domain object out of a row that violates an invariant
(`SkillGraphNode.__post_init__`/`Skill.__post_init__` run either way),
rather than silently syncing attributes.
"""

import uuid

from loom.domain.enums import Layer
from loom.domain.skill import Skill as DomainSkill
from loom.domain.skill import SkillGraphEdge as DomainEdge
from loom.domain.skill import SkillGraphNode as DomainNode
from loom.persistence.skill import Skill as SkillRow
from loom.persistence.skill import SkillGraphEdge as EdgeRow
from loom.persistence.skill import SkillGraphNode as NodeRow


def node_to_domain(row: NodeRow, *, resolved_layer: Layer | None) -> DomainNode:
    return DomainNode(
        id=row.id,
        node_key=row.node_key,
        node_type=row.node_type,
        agent_id=row.agent_id,
        skill_ref_id=row.skill_ref_id,
        tool_id=row.tool_id,
        resolved_layer=resolved_layer,
        position=row.position,
    )


def edge_to_domain(row: EdgeRow) -> DomainEdge:
    return DomainEdge(id=row.id, from_node_id=row.from_node_id, to_node_id=row.to_node_id)


def to_domain(
    row: SkillRow,
    *,
    node_rows: list[NodeRow],
    edge_rows: list[EdgeRow],
    resolved_layers: dict[uuid.UUID, Layer | None],
) -> DomainSkill:
    """Assemble one Skill Aggregate from its own row plus every node/edge
    row belonging to that specific version. `resolved_layers` (node row id
    -> that node's opaque-leaf layer, or None for a Tool reference) is
    supplied by the Repository, which is the one allowed to run the extra
    queries against Agent/Skill/Tool to produce it -- see CONTEXT.md's
    "Opaque leaf" entry."""
    skill = DomainSkill(
        id=row.id,
        entity_id=row.entity_id,
        version=row.version,
        is_current=row.is_current,
        tenant_id=row.tenant_id,
        owner_id=row.owner_id,
        created_by_id=row.created_by_id,
        approved_by_id=row.approved_by_id,
        name=row.name,
        description=row.description,
        lifecycle_state=row.lifecycle_state,
        maturity=row.maturity,
        classification=row.classification,
        created_at=row.created_at,
        approved_at=row.approved_at,
        layer=row.layer,
        kind=row.kind,
        is_entry_point=row.is_entry_point,
        atomic_content=row.atomic_content,
    )
    # Bypass add_node()'s "no graph-shaped check needed yet" path -- these
    # rows already passed every invariant when they were first added, and
    # re-running edge-by-edge cycle/layer checks while reconstituting from
    # storage would be pure waste. __post_init__ (exactly-one-of, resolved
    # layer presence) still runs on each construction below either way.
    skill.nodes = [
        node_to_domain(node_row, resolved_layer=resolved_layers[node_row.id])
        for node_row in node_rows
    ]
    skill.edges = [edge_to_domain(edge_row) for edge_row in edge_rows]
    return skill


def to_row(skill: DomainSkill) -> SkillRow:
    """New or updated row for `skill` itself -- not its nodes/edges, which
    the Repository adds separately since only genuinely new ones need a
    fresh row (existing nodes/edges never change, only gain new edges)."""
    kwargs = {
        'id': skill.id,
        'entity_id': skill.entity_id,
        'version': skill.version,
        'is_current': skill.is_current,
        'tenant_id': skill.tenant_id,
        'owner_id': skill.owner_id,
        'created_by_id': skill.created_by_id,
        'approved_by_id': skill.approved_by_id,
        'name': skill.name,
        'description': skill.description,
        'lifecycle_state': skill.lifecycle_state,
        'maturity': skill.maturity,
        'classification': skill.classification,
        'approved_at': skill.approved_at,
        'layer': skill.layer,
        'kind': skill.kind,
        'is_entry_point': skill.is_entry_point,
        'atomic_content': skill.atomic_content,
    }
    # created_at is server_default=now() -- omit rather than pass None so
    # a brand-new row still gets the DB's clock, not a NOT NULL violation.
    if skill.created_at is not None:
        kwargs['created_at'] = skill.created_at
    return SkillRow(**kwargs)


def node_to_row(node: DomainNode, *, skill_row_id: uuid.UUID) -> NodeRow:
    return NodeRow(
        id=node.id,
        skill_id=skill_row_id,
        node_key=node.node_key,
        node_type=node.node_type,
        agent_id=node.agent_id,
        skill_ref_id=node.skill_ref_id,
        tool_id=node.tool_id,
        position=node.position,
    )


def edge_to_row(edge: DomainEdge, *, skill_row_id: uuid.UUID) -> EdgeRow:
    return EdgeRow(
        id=edge.id,
        skill_id=skill_row_id,
        from_node_id=edge.from_node_id,
        to_node_id=edge.to_node_id,
    )
