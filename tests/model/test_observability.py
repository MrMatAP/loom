import decimal

import pytest
import sqlalchemy as sa

from loom.model.capability import Capability
from loom.model.observability import Metric
from loom.model.schemas.capability import CapabilityCreate
from loom.model.schemas.observability import MetricCreate
from loom.model.tenant import Principal, Tenant


def test_metric_scopes_to_exactly_one_entity(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(
        tenant_id=tenant.id,
        kind='user',
        external_id='ada',
    )
    session.add(principal)
    session.commit()

    capability = Capability(
        **CapabilityCreate(
            slug='ticket-triage',
            name='Ticket Triage',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
        ).model_dump()
    )
    session.add(capability)
    session.commit()

    metric = Metric(
        **MetricCreate(
            tenant_id=tenant.id,
            capability_id=capability.id,
            metric_name='realization_score',
            value=decimal.Decimal('0.87'),
            is_realization_score=True,
        ).model_dump()
    )
    session.add(metric)
    session.commit()
    assert metric.value == decimal.Decimal('0.870000')

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            Metric(
                tenant_id=tenant.id,
                metric_name='orphan',
                value=decimal.Decimal(1),
            )
        )
        session.commit()
