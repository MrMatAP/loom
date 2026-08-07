import uuid

import pytest
import sqlalchemy as sa

from loom.model.base import (
    Base,
    VersionedEntityMixin,
    current_version_index,
    exactly_one_of,
)
from loom.model.tenant import Principal, Tenant


class _Widget(Base, VersionedEntityMixin):
    __tablename__ = 'widget'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_widget_entity_version'),
        current_version_index('widget'),
    )


def _make_tenant_and_principal(session) -> tuple[Tenant, Principal]:
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(
        tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada'
    )
    session.add(principal)
    session.commit()
    return tenant, principal


def test_versioned_entity_defaults_and_fk(session):
    tenant, principal = _make_tenant_and_principal(session)
    widget = _Widget(
        slug='w',
        name='Widget',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
    )
    session.add(widget)
    session.commit()
    assert widget.version == 1
    assert widget.is_current is True
    assert widget.lifecycle_state == 'draft'


def test_only_one_current_row_per_entity_id(session):
    tenant, principal = _make_tenant_and_principal(session)
    entity_id = uuid.uuid4()
    session.add(
        _Widget(
            entity_id=entity_id,
            version=1,
            is_current=True,
            slug='w',
            name='Widget v1',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
        )
    )
    session.commit()
    session.add(
        _Widget(
            entity_id=entity_id,
            version=2,
            is_current=True,
            slug='w',
            name='Widget v2',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()


def test_exactly_one_of_rejects_zero_and_two():
    assert exactly_one_of('a', 'b') == (
        '((a IS NOT NULL AND b IS NULL) OR (b IS NOT NULL AND a IS NULL))'
    )
