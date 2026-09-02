from loom.persistence.tenant import Principal, Tenant
from loom.persistence.tool import Tool
from loom.schemas.tool import ToolCreate, ToolRead


def test_tool_round_trip(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', external_id='ada')
    session.add(principal)
    session.commit()

    payload = ToolCreate(
        name='Send Email',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        invocation_spec={'method': 'POST', 'path': '/v1/email'},
    )
    tool = Tool(**payload.model_dump())
    session.add(tool)
    session.commit()

    read = ToolRead.model_validate(tool)
    assert read.invocation_spec['method'] == 'POST'
    assert read.auth_binding_id is None
