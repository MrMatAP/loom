from loom.model.agent import Agent
from loom.model.enums import Layer, MemoryScope
from loom.model.schemas.agent import AgentCreate, AgentRead
from loom.model.tenant import Principal, Tenant


def _tenant_and_principal(session) -> tuple[Tenant, Principal]:
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(
        tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada'
    )
    session.add(principal)
    session.commit()
    return tenant, principal


def test_agent_round_trip(session):
    tenant, principal = _tenant_and_principal(session)
    payload = AgentCreate(
        slug='triage-agent',
        name='Triage Agent',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        layer=Layer.BUSINESS_OPS,
        llm_config={'provider': 'anthropic', 'model': 'claude-opus-5'},
        prompt='You triage incoming tickets.',
        memory_scope=MemoryScope.SESSION,
    )
    agent = Agent(**payload.model_dump())
    session.add(agent)
    session.commit()

    read = AgentRead.model_validate(agent)
    assert read.layer == Layer.BUSINESS_OPS
    assert read.llm_config['model'] == 'claude-opus-5'
    assert read.permission_boundary == {}
