"""The Skill Aggregate -- see CONTEXT.md's "Aggregate (root)" entry.

Pilot for the full DDD separation (docs/adr/0001-ddd-separation-for-catalog-domain.md):
Skill is the one Aggregate with a real, previously-unenforced invariant --
CLAUDE.md's layer-descent + cycle-detection rule for its composite graph --
so this module is where that rule finally gets enforced, rather than living
only as a `Layer` column nobody reads.

Nothing in this module imports SQLAlchemy, FastAPI, or pydantic. It knows
nothing about how a Skill is stored or served.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid

from loom.domain.enums import (
    Classification,
    GraphNodeType,
    Layer,
    LifecycleState,
    MaturityLevel,
    SkillKind,
)
from loom.domain.errors import InvariantViolation, NotFoundError, ValidationError
from loom.domain.lifecycle import is_legal_transition

# Rank for the "may call same layer or lower" rule (CLAUDE.md's layer
# table). Not the enum's declaration order -- kept as an explicit mapping
# so a future reordering of the Layer enum can't silently change descent
# semantics.
_LAYER_RANK: dict[Layer, int] = {
    Layer.INFRA_OPS: 0,
    Layer.BUSINESS_TECH: 1,
    Layer.BUSINESS_OPS: 2,
}


class NodeReferenceError(ValidationError):
    """A graph node's exactly-one-of(agent/skill/tool) reference doesn't
    match its declared `node_type`, or names more/fewer than one target."""


class AtomicContentError(ValidationError):
    """An atomic Skill is missing `atomic_content`, or a composite one sets it."""


class LayerViolationError(InvariantViolation):
    """An edge would call upward across layers (e.g. InfraOps -> BusinessOps),
    violating CLAUDE.md's "may call same-layer or lower" rule."""


class CycleError(InvariantViolation):
    """An edge would close a cycle in the Skill's graph. Layer-descent
    legality alone doesn't rule this out -- same-layer edges are legal and
    can still form a cycle -- so this is checked independently."""


class IllegalTransitionError(InvariantViolation):
    """A lifecycle transition is not structurally legal, or targets a
    version that is no longer current."""


@dataclasses.dataclass
class SkillGraphNode:
    """One node in a composite Skill's graph: a reference to an Agent,
    another Skill, or a Tool.

    `resolved_layer` is the *opaque leaf* value (see CONTEXT.md) this node
    presents to layer-descent checking: the referenced entity's own
    already-stored `layer`, or `None` for a Tool reference -- Tool is
    never layered and is always a legal call target (CLAUDE.md's layer
    table lists it as reachable from every layer). The Skill aggregate
    never reaches into the referenced entity's own internals to get this;
    it's resolved once, by the caller, from that entity's own current
    state, and handed in.
    """

    node_key: str
    node_type: GraphNodeType
    agent_id: uuid.UUID | None = None
    skill_ref_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    resolved_layer: Layer | None = None
    position: dict | None = None
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        refs = [self.agent_id, self.skill_ref_id, self.tool_id]
        if sum(ref is not None for ref in refs) != 1:
            raise NodeReferenceError(
                f'Node {self.node_key!r} must reference exactly one of '
                f'agent_id/skill_ref_id/tool_id'
            )
        expected = {
            GraphNodeType.AGENT: self.agent_id,
            GraphNodeType.SKILL: self.skill_ref_id,
            GraphNodeType.TOOL: self.tool_id,
        }[self.node_type]
        if expected is None:
            raise NodeReferenceError(
                f'Node {self.node_key!r} declares node_type={self.node_type} '
                f'but its matching reference id is unset'
            )
        # A Tool is never layered -- it's the unconditionally-legal,
        # opaque leaf every layer may call (CLAUDE.md's layer table).
        # A resolved_layer on a Tool-referencing node would be a caller
        # bug, not user input to reject gracefully: it means whoever
        # resolved this node handed us a layer for something that has none.
        if self.node_type == GraphNodeType.TOOL:
            assert self.resolved_layer is None, (
                'Tool-referencing nodes are never layered'
            )
        elif self.resolved_layer is None:
            raise NodeReferenceError(
                f'Node {self.node_key!r} references a {self.node_type} but '
                f'was not resolved to a layer'
            )


@dataclasses.dataclass
class SkillGraphEdge:
    """A directed 'may call' edge between two nodes of the same Skill's graph."""

    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)


@dataclasses.dataclass
class Skill:
    """Composable capability: atomic (prompt/code) or composite (a graph
    of Agent/Skill/Tool nodes). The Aggregate root for itself and its own
    `nodes`/`edges` -- see CONTEXT.md's "Aggregate (root)" entry: nothing
    outside this class ever mutates `nodes`/`edges` directly."""

    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    created_by_id: uuid.UUID
    name: str
    layer: Layer
    kind: SkillKind
    description: str | None = None
    is_entry_point: bool = False
    atomic_content: dict | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    maturity: MaturityLevel = MaturityLevel.EXPERIMENTAL
    classification: Classification = Classification.INTERNAL
    entity_id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)
    version: int = 1
    is_current: bool = True
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)
    created_at: datetime.datetime | None = None
    approved_at: datetime.datetime | None = None
    approved_by_id: uuid.UUID | None = None
    nodes: list[SkillGraphNode] = dataclasses.field(default_factory=list)
    edges: list[SkillGraphEdge] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind == SkillKind.ATOMIC and self.atomic_content is None:
            raise AtomicContentError('An atomic Skill requires atomic_content')
        if self.kind == SkillKind.COMPOSITE and self.atomic_content is not None:
            raise AtomicContentError('A composite Skill must not set atomic_content')

    # -- versioning ---------------------------------------------------

    def new_version(
        self,
        *,
        created_by_id: uuid.UUID,
        name: str,
        layer: Layer,
        kind: SkillKind,
        description: str | None = None,
        is_entry_point: bool = False,
        atomic_content: dict | None = None,
    ) -> Skill:
        """Content changes always create a new, immutable version row --
        never an in-place edit (see CONTEXT.md / the registry data-model
        design doc). Marks `self` no-longer-current as bookkeeping on the
        prior row, same as `VersionedEntityMixin.is_current`'s docstring
        describes, and returns the new current version. A fresh graph:
        nodes/edges belong to one specific version, never carried forward.
        """
        self.is_current = False
        return Skill(
            entity_id=self.entity_id,
            version=self.version + 1,
            is_current=True,
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,  # ownership carries over, never re-specified
            created_by_id=created_by_id,
            name=name,
            description=description,
            layer=layer,
            kind=kind,
            is_entry_point=is_entry_point,
            atomic_content=atomic_content,
        )

    def transition(
        self, to_state: LifecycleState, *, actor_id: uuid.UUID, now: datetime.datetime
    ) -> None:
        """Mutate `lifecycle_state` in place on this (current) version row --
        a deliberate, documented deviation from "content changes always
        version" for governance-state changes specifically (see the
        catalog-api design doc's flagged deviation)."""
        if not is_legal_transition(self.lifecycle_state, to_state):
            raise IllegalTransitionError(
                f'{self.lifecycle_state} -> {to_state} is not a legal transition'
            )
        self.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            self.approved_by_id = actor_id
            self.approved_at = now

    # -- descriptive fields: no invariant, so a generic setter is fine --

    def update(self, **fields: object) -> None:
        """Mutate purely descriptive fields with no business rule attached
        (name, description, maturity, classification) -- the generic half
        of the hybrid mutation API (CONTEXT.md's `update(**fields)`
        decision). `layer`/`kind`/lifecycle/graph edits all go through
        their own named, invariant-checked methods instead."""
        allowed = {'name', 'description', 'maturity', 'classification'}
        unknown = fields.keys() - allowed
        if unknown:
            raise ValidationError(
                f'update() cannot set {sorted(unknown)} -- '
                f'those fields have dedicated methods'
            )
        for field, value in fields.items():
            setattr(self, field, value)

    # -- graph -----------------------------------------------------------

    def _node(self, node_id: uuid.UUID) -> SkillGraphNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise NotFoundError(f'Node {node_id} does not belong to Skill {self.entity_id}')

    def add_node(self, node: SkillGraphNode) -> SkillGraphNode:
        """Node-level validation (exactly-one-of, node_type/reference
        agreement, resolved_layer presence) already happened in
        `SkillGraphNode.__post_init__` -- nothing graph-shaped to check
        yet with only one node in play."""
        self.nodes.append(node)
        return node

    def add_edge(self, from_node_id: uuid.UUID, to_node_id: uuid.UUID) -> SkillGraphEdge:
        """The invariant this whole pilot exists to enforce: layer-descent
        (may call same layer or lower; Tool is always legal) and
        acyclicity, checked independently (CLAUDE.md: "cycle detection is
        a first-class check, not an implied side effect of the layer
        rule" -- same-layer edges are legal and can still close a cycle).
        """
        from_node = self._node(from_node_id)
        to_node = self._node(to_node_id)

        # A Tool is a leaf: CLAUDE.md's layer table only ever lists Tool as
        # something a layer *may call*, never as a caller itself -- nothing
        # in the entity model gives a deterministic automation endpoint the
        # ability to invoke something else in the graph.
        if from_node.node_type == GraphNodeType.TOOL:
            raise LayerViolationError(
                f'Node {from_node.node_key!r} is a Tool reference and cannot '
                f'originate an edge'
            )

        if to_node.resolved_layer is not None:
            from_rank = _LAYER_RANK[from_node.resolved_layer]
            to_rank = _LAYER_RANK[to_node.resolved_layer]
            if from_rank < to_rank:
                raise LayerViolationError(
                    f'{from_node.node_key!r} ({from_node.resolved_layer}) cannot '
                    f'call {to_node.node_key!r} ({to_node.resolved_layer}): '
                    f'a layer may only call itself or a lower layer'
                )
        # else: to_node is a Tool -- always a legal target, no layer check.

        if self._creates_cycle(from_node_id, to_node_id):
            raise CycleError(
                f'Edge {from_node.node_key!r} -> {to_node.node_key!r} would '
                f'close a cycle'
            )

        edge = SkillGraphEdge(from_node_id=from_node_id, to_node_id=to_node_id)
        self.edges.append(edge)
        return edge

    def _creates_cycle(self, from_node_id: uuid.UUID, to_node_id: uuid.UUID) -> bool:
        """True iff adding from_node_id -> to_node_id would create a cycle,
        i.e. `from_node_id` is already reachable from `to_node_id` along
        existing edges (a self-loop is the degenerate case: reachable in
        zero steps)."""
        if from_node_id == to_node_id:
            return True
        adjacency: dict[uuid.UUID, list[uuid.UUID]] = {}
        for edge in self.edges:
            adjacency.setdefault(edge.from_node_id, []).append(edge.to_node_id)
        stack = [to_node_id]
        seen: set[uuid.UUID] = set()
        while stack:
            current = stack.pop()
            if current == from_node_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adjacency.get(current, ()))
        return False
