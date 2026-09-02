import pytest
import sqlalchemy as sa

from loom.domain.enums import EnvironmentKind
from loom.persistence.environment import Environment
from loom.persistence.tenant import Tenant
from loom.schemas.environment import EnvironmentCreate, EnvironmentRead


def test_environment_round_trip(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()

    env = Environment(
        tenant_id=tenant.id,
        **EnvironmentCreate(
            name='prod',
            kind=EnvironmentKind.PRODUCTION,
            compute_boundary_ref='vpc-prod-compute',
            network_boundary_ref='vpc-prod-net',
        ).model_dump(),
    )
    session.add(env)
    session.commit()

    read = EnvironmentRead.model_validate(env)
    assert read.kind == EnvironmentKind.PRODUCTION


def test_environment_name_unique_per_tenant(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()

    session.add(
        Environment(
            tenant_id=tenant.id,
            name='prod',
            kind=EnvironmentKind.PRODUCTION,
            compute_boundary_ref='a',
            network_boundary_ref='b',
        )
    )
    session.commit()
    session.add(
        Environment(
            tenant_id=tenant.id,
            name='prod',
            kind=EnvironmentKind.SANDBOX,
            compute_boundary_ref='c',
            network_boundary_ref='d',
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()
