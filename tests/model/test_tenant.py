import uuid

import pytest
import sqlalchemy as sa

from loom.model.enums import PrincipalKind
from loom.model.schemas.tenant import (
    PrincipalCreate,
    PrincipalRead,
    TenantCreate,
    TenantRead,
)
from loom.model.tenant import Principal, Tenant


def test_tenant_round_trip(session):
    tenant = Tenant(**TenantCreate(slug='acme', name='Acme Corp').model_dump())
    session.add(tenant)
    session.commit()

    read = TenantRead.model_validate(tenant)
    assert read.slug == 'acme'
    assert isinstance(read.id, uuid.UUID)


def test_principal_unique_external_id_per_tenant(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()

    principal = Principal(
        **PrincipalCreate(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            external_id='ada@acme.example',
        ).model_dump()
    )
    session.add(principal)
    session.commit()

    read = PrincipalRead.model_validate(principal)
    assert read.kind == PrincipalKind.USER

    session.add(
        Principal(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            external_id='ada@acme.example',
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()
