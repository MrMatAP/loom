import decimal

import pytest
import sqlalchemy as sa

from loom.model.agent import Agent
from loom.model.capability import Capability, CapabilityRealization
from loom.model.enums import Layer, MemoryScope, RealizingEntityType
from loom.model.schemas.agent import AgentCreate
from loom.model.schemas.capability import CapabilityCreate, CapabilityRealizationCreate
from loom.model.tenant import Principal, Tenant


def test_capability_realization_exactly_one_realizer(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(
        tenant_id=tenant.id,
        kind='user',
        display_name='Ada',
        external_id='ada',
    )
    session.add(principal)
    session.commit()

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

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            CapabilityRealization(
                capability_id=capability.id,
                realizing_entity_type=RealizingEntityType.AGENT,
                realizing_agent_id=agent.id,
                realizing_skill_id=None,
                contribution_weight=decimal.Decimal('1.0'),
            )
        )
        # Force the violation explicitly since both agent_id set alone is valid;
        # exercise the zero-set case instead:
        session.rollback()
        session.add(
            CapabilityRealization(
                capability_id=capability.id,
                realizing_entity_type=RealizingEntityType.AGENT,
                contribution_weight=decimal.Decimal('1.0'),
            )
        )
        session.commit()
