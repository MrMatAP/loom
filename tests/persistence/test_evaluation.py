import pytest
import sqlalchemy as sa

from loom.domain.enums import (
    EvalRunStatus,
    Layer,
    LifecycleState,
    MemoryScope,
    VersionedEntityKind,
)
from loom.persistence.agent import Agent
from loom.persistence.evaluation import EvalRun, EvalSuite
from loom.persistence.tenant import Principal, Tenant
from loom.schemas.agent import AgentCreate
from loom.schemas.evaluation import EvalRunCreate, EvalSuiteCreate


def test_eval_run_requires_exactly_one_target(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', external_id='ada')
    session.add(principal)
    session.commit()

    suite = EvalSuite(
        **EvalSuiteCreate(
            tenant_id=tenant.id,
            slug='agent-quality',
            name='Agent Quality',
            target_entity_type=VersionedEntityKind.AGENT,
            criteria={'min_accuracy': 0.9},
            created_by_id=principal.id,
        ).model_dump()
    )
    session.add(suite)
    session.commit()

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            EvalRun(
                eval_suite_id=suite.id,
                target_entity_type=VersionedEntityKind.AGENT,
                status=EvalRunStatus.PENDING,
                results={},
                triggered_by_id=principal.id,
            )
        )
        session.commit()


def test_eval_run_gates_lifecycle_transition(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', external_id='ada')
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

    suite = EvalSuite(
        tenant_id=tenant.id,
        slug='agent-quality',
        name='Agent Quality',
        target_entity_type=VersionedEntityKind.AGENT,
        criteria={},
        created_by_id=principal.id,
    )
    session.add(suite)
    session.commit()

    run = EvalRun(
        **EvalRunCreate(
            eval_suite_id=suite.id,
            target_entity_type=VersionedEntityKind.AGENT,
            target_agent_id=agent.id,
            status=EvalRunStatus.PASSED,
            results={'accuracy': 0.95},
            triggered_by_id=principal.id,
            gates_transition_to=LifecycleState.APPROVED,
        ).model_dump()
    )
    session.add(run)
    session.commit()
    assert run.status == EvalRunStatus.PASSED
    assert run.gates_transition_to == LifecycleState.APPROVED
