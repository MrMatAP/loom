import pytest
import sqlalchemy as sa

from loom.model.agent import Agent
from loom.model.enums import GraphNodeType, Layer, MemoryScope, SkillKind
from loom.model.schemas.agent import AgentCreate
from loom.model.schemas.skill import SkillCreate
from loom.model.skill import Skill, SkillGraphEdge, SkillGraphNode
from loom.model.tenant import Principal, Tenant


def _tenant_and_principal(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(
        tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada'
    )
    session.add(principal)
    session.commit()
    return tenant, principal


def test_atomic_skill_requires_content(session):
    tenant, principal = _tenant_and_principal(session)
    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            Skill(
                slug='draft-reply',
                name='Draft Reply',
                tenant_id=tenant.id,
                owner_id=principal.id,
                created_by_id=principal.id,
                layer=Layer.BUSINESS_OPS,
                kind=SkillKind.ATOMIC,
                atomic_content=None,
            )
        )
        session.commit()


def test_composite_skill_graph(session):
    tenant, principal = _tenant_and_principal(session)
    agent = Agent(
        **AgentCreate(
            slug='triage-agent',
            name='Triage Agent',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
            layer=Layer.BUSINESS_OPS,
            llm_config={},
            prompt='p',
            memory_scope=MemoryScope.NONE,
        ).model_dump()
    )
    session.add(agent)
    session.commit()

    skill = Skill(
        **SkillCreate(
            slug='triage-flow',
            name='Triage Flow',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
            layer=Layer.BUSINESS_OPS,
            kind=SkillKind.COMPOSITE,
            is_entry_point=True,
        ).model_dump()
    )
    session.add(skill)
    session.commit()

    start = SkillGraphNode(
        skill_id=skill.id,
        node_key='start',
        node_type=GraphNodeType.AGENT,
        agent_id=agent.id,
    )
    session.add(start)
    session.commit()

    session.add(
        SkillGraphEdge(skill_id=skill.id, from_node_id=start.id, to_node_id=start.id)
    )
    session.commit()

    assert session.query(SkillGraphEdge).count() == 1
