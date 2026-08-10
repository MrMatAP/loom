import pytest
import sqlalchemy as sa

from loom.model.enums import ModelProtocol
from loom.model.model_endpoint import ModelEndpoint
from loom.model.schemas.model_endpoint import ModelEndpointCreate, ModelEndpointRead
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


def test_model_endpoint_round_trip(session):
    tenant, principal = _tenant_and_principal(session)
    payload = ModelEndpointCreate(
        slug='self-hosted-llama',
        name='Self-Hosted Llama',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        protocol=ModelProtocol.OPENAI_COMPATIBLE,
        base_url='https://llm.internal.example/v1',
        model='meta-llama/Llama-3-70b',
    )
    model_endpoint = ModelEndpoint(**payload.model_dump())
    session.add(model_endpoint)
    session.commit()

    read = ModelEndpointRead.model_validate(model_endpoint)
    assert read.protocol == ModelProtocol.OPENAI_COMPATIBLE
    assert read.base_url == 'https://llm.internal.example/v1'
    assert read.model == 'meta-llama/Llama-3-70b'
    assert read.auth_binding_id is None


def test_openai_compatible_requires_base_url(session):
    tenant, principal = _tenant_and_principal(session)
    model_endpoint = ModelEndpoint(
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        slug='no-base-url',
        name='No Base URL',
        protocol=ModelProtocol.OPENAI_COMPATIBLE,
        base_url=None,
        model='some-model',
    )
    session.add(model_endpoint)
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()


def test_anthropic_messages_does_not_require_base_url(session):
    tenant, principal = _tenant_and_principal(session)
    model_endpoint = ModelEndpoint(
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        slug='claude',
        name='Claude',
        protocol=ModelProtocol.ANTHROPIC_MESSAGES,
        base_url=None,
        model='claude-opus-5',
    )
    session.add(model_endpoint)
    session.commit()
    assert model_endpoint.base_url is None
