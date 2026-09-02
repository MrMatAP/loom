"""Pure domain tests for the Skill Aggregate -- no DB, no SQLAlchemy, no
FastAPI: the whole point of loom.domain being persistence-ignorant (see
CONTEXT.md's "Domain object" entry) is that these run without any of that.
"""

import datetime
import uuid

import pytest

from loom.domain.enums import GraphNodeType, Layer, LifecycleState, SkillKind
from loom.domain.errors import IllegalTransitionError, NotFoundError, ValidationError
from loom.domain.skill import (
    AtomicContentError,
    CycleError,
    LayerViolationError,
    NodeReferenceError,
    Skill,
    SkillGraphNode,
)


def _skill(layer: Layer = Layer.BUSINESS_OPS, **overrides) -> Skill:
    defaults = {
        'tenant_id': uuid.uuid4(),
        'owner_id': uuid.uuid4(),
        'created_by_id': uuid.uuid4(),
        'name': 'Triage Flow',
        'layer': layer,
        'kind': SkillKind.COMPOSITE,
    }
    defaults.update(overrides)
    return Skill(**defaults)


def _node(node_type: GraphNodeType, layer: Layer | None, **overrides) -> SkillGraphNode:
    ref_field = {
        GraphNodeType.AGENT: 'agent_id',
        GraphNodeType.SKILL: 'skill_ref_id',
        GraphNodeType.TOOL: 'tool_id',
    }[node_type]
    kwargs = {
        'node_key': overrides.pop('node_key', node_type.value),
        'node_type': node_type,
        ref_field: overrides.pop(ref_field, uuid.uuid4()),
        'resolved_layer': layer,
    }
    kwargs.update(overrides)
    return SkillGraphNode(**kwargs)


def test_atomic_skill_requires_content():
    with pytest.raises(AtomicContentError):
        _skill(kind=SkillKind.ATOMIC, atomic_content=None)


def test_composite_skill_rejects_atomic_content():
    with pytest.raises(AtomicContentError):
        _skill(kind=SkillKind.COMPOSITE, atomic_content={'x': 1})


def test_atomic_skill_with_content_is_fine():
    skill = _skill(kind=SkillKind.ATOMIC, atomic_content={'prompt': 'hi'})
    assert skill.atomic_content == {'prompt': 'hi'}


@pytest.mark.parametrize(
    'refs',
    [
        {},
        {'agent_id': uuid.uuid4(), 'tool_id': uuid.uuid4()},
    ],
)
def test_node_requires_exactly_one_reference(refs):
    with pytest.raises(NodeReferenceError):
        SkillGraphNode(
            node_key='n',
            node_type=GraphNodeType.AGENT,
            resolved_layer=Layer.BUSINESS_OPS,
            **refs,
        )


def test_node_type_must_match_the_populated_reference():
    with pytest.raises(NodeReferenceError):
        SkillGraphNode(
            node_key='n',
            node_type=GraphNodeType.AGENT,
            tool_id=uuid.uuid4(),
        )


def test_tool_node_must_not_carry_a_resolved_layer():
    with pytest.raises(AssertionError):
        SkillGraphNode(
            node_key='n',
            node_type=GraphNodeType.TOOL,
            tool_id=uuid.uuid4(),
            resolved_layer=Layer.INFRA_OPS,
        )


def test_non_tool_node_requires_a_resolved_layer():
    with pytest.raises(NodeReferenceError):
        SkillGraphNode(
            node_key='n', node_type=GraphNodeType.AGENT, agent_id=uuid.uuid4()
        )


def test_add_edge_same_layer_is_legal():
    skill = _skill()
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='a'))
    b = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='b'))
    edge = skill.add_edge(a.id, b.id)
    assert edge in skill.edges


def test_add_edge_downward_is_legal():
    skill = _skill()
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='a'))
    b = skill.add_node(_node(GraphNodeType.SKILL, Layer.INFRA_OPS, node_key='b'))
    skill.add_edge(a.id, b.id)  # no raise


def test_add_edge_upward_is_a_layer_violation():
    skill = _skill()
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.INFRA_OPS, node_key='a'))
    b = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='b'))
    with pytest.raises(LayerViolationError):
        skill.add_edge(a.id, b.id)


def test_add_edge_to_a_tool_is_always_legal():
    skill = _skill()
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.INFRA_OPS, node_key='a'))
    tool = skill.add_node(_node(GraphNodeType.TOOL, None, node_key='tool'))
    skill.add_edge(a.id, tool.id)  # no raise -- Tool is an opaque, always-legal leaf


def test_add_edge_from_a_tool_is_rejected():
    skill = _skill()
    tool = skill.add_node(_node(GraphNodeType.TOOL, None, node_key='tool'))
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.INFRA_OPS, node_key='a'))
    with pytest.raises(LayerViolationError):
        skill.add_edge(tool.id, a.id)


def test_add_edge_self_loop_is_a_cycle():
    skill = _skill()
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='a'))
    with pytest.raises(CycleError):
        skill.add_edge(a.id, a.id)


def test_add_edge_closing_a_two_node_cycle_is_rejected():
    skill = _skill()
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='a'))
    b = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='b'))
    skill.add_edge(a.id, b.id)
    with pytest.raises(CycleError):
        skill.add_edge(b.id, a.id)


def test_add_edge_unknown_node_raises_not_found():
    skill = _skill()
    a = skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='a'))
    with pytest.raises(NotFoundError):
        skill.add_edge(a.id, uuid.uuid4())


def test_transition_to_a_legal_state_succeeds():
    skill = _skill()
    skill.transition(
        LifecycleState.IN_REVIEW,
        actor_id=uuid.uuid4(),
        now=datetime.datetime.now(datetime.UTC),
    )
    assert skill.lifecycle_state == LifecycleState.IN_REVIEW


def test_transition_to_an_illegal_state_raises():
    skill = _skill()
    with pytest.raises(IllegalTransitionError):
        skill.transition(
            LifecycleState.PUBLISHED,
            actor_id=uuid.uuid4(),
            now=datetime.datetime.now(datetime.UTC),
        )


def test_transition_to_approved_records_the_approver():
    skill = _skill()
    skill.lifecycle_state = LifecycleState.IN_REVIEW
    actor = uuid.uuid4()
    now = datetime.datetime.now(datetime.UTC)
    skill.transition(LifecycleState.APPROVED, actor_id=actor, now=now)
    assert skill.approved_by_id == actor
    assert skill.approved_at == now


def test_new_version_marks_the_prior_row_not_current():
    skill = _skill()
    skill.add_node(_node(GraphNodeType.AGENT, Layer.BUSINESS_OPS, node_key='a'))
    v2 = skill.new_version(
        created_by_id=uuid.uuid4(),
        name='Triage Flow v2',
        layer=Layer.BUSINESS_OPS,
        kind=SkillKind.COMPOSITE,
    )
    assert skill.is_current is False
    assert v2.is_current is True
    assert v2.entity_id == skill.entity_id
    assert v2.version == skill.version + 1
    assert v2.owner_id == skill.owner_id
    assert v2.nodes == []  # a fresh version starts with a fresh graph


def test_update_mutates_descriptive_fields_only():
    skill = _skill()
    skill.update(name='Renamed', description='New description')
    assert skill.name == 'Renamed'
    assert skill.description == 'New description'


def test_update_rejects_fields_with_dedicated_methods():
    skill = _skill()
    with pytest.raises(ValidationError):
        skill.update(lifecycle_state=LifecycleState.PUBLISHED)
