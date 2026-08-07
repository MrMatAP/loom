import decimal

import pytest
import sqlalchemy as sa

from loom.model.agent import Agent
from loom.model.capability import Capability, CapabilityRealization
from loom.model.enums import Layer, MemoryScope, RealizingEntityType, SkillKind
from loom.model.schemas.agent import AgentCreate
from loom.model.schemas.capability import CapabilityCreate, CapabilityRealizationCreate
from loom.model.schemas.skill import SkillCreate
from loom.model.skill import Skill
from loom.model.tenant import Principal, Tenant


def _tenant_and_principal(session):
    """Helper to create tenant and principal for tests."""
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(
        tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada'
    )
    session.add(principal)
    session.commit()
    return tenant, principal


def test_capability_realization_single_realizer_valid(session):
    """Successful creation with exactly one realizer (agent)."""
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

    capability = Capability(
        **CapabilityCreate(
            slug='ticket-triage',
            name='Ticket Triage',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
            target_metrics=[{'metric_name': 'accuracy', 'target_value': 0.9}],
        ).model_dump()
    )
    session.add(capability)
    session.commit()

    realization = CapabilityRealization(
        **CapabilityRealizationCreate(
            capability_id=capability.id,
            realizing_entity_type=RealizingEntityType.AGENT,
            realizing_agent_id=agent.id,
            contribution_weight=decimal.Decimal('1.0'),
        ).model_dump()
    )
    session.add(realization)
    session.commit()
    assert realization.contribution_weight == decimal.Decimal('1.0')


def test_capability_realization_zero_realizers_rejected(session):
    """Check constraint rejects zero realizers set."""
    tenant, principal = _tenant_and_principal(session)

    capability = Capability(
        **CapabilityCreate(
            slug='ticket-triage',
            name='Ticket Triage',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
            target_metrics=[{'metric_name': 'accuracy', 'target_value': 0.9}],
        ).model_dump()
    )
    session.add(capability)
    session.commit()

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            CapabilityRealization(
                capability_id=capability.id,
                realizing_entity_type=RealizingEntityType.AGENT,
                contribution_weight=decimal.Decimal('1.0'),
            )
        )
        session.commit()


def test_capability_realization_multi_realizers_rejected(session):
    """Check constraint rejects multiple realizers set."""
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
        ).model_dump()
    )
    session.add(skill)
    session.commit()

    capability = Capability(
        **CapabilityCreate(
            slug='ticket-triage',
            name='Ticket Triage',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
            target_metrics=[{'metric_name': 'accuracy', 'target_value': 0.9}],
        ).model_dump()
    )
    session.add(capability)
    session.commit()

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            CapabilityRealization(
                capability_id=capability.id,
                realizing_entity_type=RealizingEntityType.AGENT,
                realizing_agent_id=agent.id,
                realizing_skill_id=skill.id,
                contribution_weight=decimal.Decimal('1.0'),
            )
        )
        session.commit()
