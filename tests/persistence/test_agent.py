from loom.domain.enums import Layer, MemoryScope, ModelProtocol
from loom.persistence.agent import Agent
from loom.persistence.model_endpoint import ModelEndpoint
from loom.persistence.tenant import Principal, Tenant
from loom.schemas.agent import AgentCreate, AgentRead
from loom.schemas.model_endpoint import ModelEndpointCreate


def _tenant_and_principal(session) -> tuple[Tenant, Principal]:
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', external_id='ada')
    session.add(principal)
    session.commit()
    return tenant, principal


def test_agent_round_trip(session):
    tenant, principal = _tenant_and_principal(session)
    endpoint_payload = ModelEndpointCreate(
        name='Claude',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        protocol=ModelProtocol.ANTHROPIC_MESSAGES,
        model='claude-opus-5',
    )
    model_endpoint = ModelEndpoint(**endpoint_payload.model_dump())
    session.add(model_endpoint)
    session.commit()

    payload = AgentCreate(
        name='Triage Agent',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        layer=Layer.BUSINESS_OPS,
        model_binding_id=model_endpoint.entity_id,
        # Per-agent invocation overrides for whatever model_binding_id
        # points at -- not the endpoint's identity, which lives on
        # ModelEndpoint itself.
        llm_config={'temperature': 0.2},
        prompt='You triage incoming tickets.',
        memory_scope=MemoryScope.SESSION,
    )
    agent = Agent(**payload.model_dump())
    session.add(agent)
    session.commit()

    read = AgentRead.model_validate(agent)
    assert read.layer == Layer.BUSINESS_OPS
    assert read.model_binding_id == model_endpoint.entity_id
    assert read.llm_config['temperature'] == 0.2
    assert read.permission_boundary == {}


def test_agent_can_exist_without_a_model_binding(session):
    """A Draft agent can exist before a model is chosen."""
    tenant, principal = _tenant_and_principal(session)
    payload = AgentCreate(
        name='Unbound Agent',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        layer=Layer.BUSINESS_OPS,
        llm_config={},
        prompt='Draft prompt.',
        memory_scope=MemoryScope.SESSION,
    )
    agent = Agent(**payload.model_dump())
    session.add(agent)
    session.commit()

    read = AgentRead.model_validate(agent)
    assert read.model_binding_id is None
