import pytest
import sqlalchemy as sa

from loom.model.enums import AuditDecision, PolicyEffect, PolicyScopeType
from loom.model.governance import AuditEvent, Policy, RoleBinding
from loom.model.schemas.governance import (
    AuditEventCreate,
    PolicyCreate,
    RoleBindingCreate,
)
from loom.model.tenant import Principal, Tenant


def _tenant_and_two_principals(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    grantor = Principal(
        tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada'
    )
    subject = Principal(
        tenant_id=tenant.id, kind='agent', display_name='Bot', external_id='bot'
    )
    session.add_all([grantor, subject])
    session.commit()
    return tenant, grantor, subject


def test_delegated_role_binding_requires_permission_subset(session):
    tenant, grantor, subject = _tenant_and_two_principals(session)
    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            RoleBinding(
                tenant_id=tenant.id,
                principal_id=subject.id,
                role='reader',
                scope_type=PolicyScopeType.ENTITY,
                scope_ref={},
                delegated_from_principal_id=grantor.id,
                permission_subset=None,
                created_by_id=grantor.id,
            )
        )
        session.commit()


def test_policy_role_binding_audit_round_trip(session):
    tenant, grantor, subject = _tenant_and_two_principals(session)
    policy = Policy(
        **PolicyCreate(
            tenant_id=tenant.id,
            name='Deny write outside sandbox',
            effect=PolicyEffect.DENY,
            scope_type=PolicyScopeType.ENVIRONMENT,
            rule={'condition': 'env != sandbox'},
            created_by_id=grantor.id,
        ).model_dump()
    )
    session.add(policy)
    session.commit()

    binding = RoleBinding(
        **RoleBindingCreate(
            tenant_id=tenant.id,
            principal_id=subject.id,
            role='reader',
            scope_type=PolicyScopeType.ENTITY,
            scope_ref={'entity_id': str(subject.id)},
            created_by_id=grantor.id,
        ).model_dump()
    )
    session.add(binding)
    session.commit()

    event = AuditEvent(
        **AuditEventCreate(
            tenant_id=tenant.id,
            actor_principal_id=subject.id,
            action='invoke',
            entity_type='tool',
            decision=AuditDecision.DENY,
            policy_id=policy.id,
            details={'reason': 'outside sandbox'},
        ).model_dump()
    )
    session.add(event)
    session.commit()
    assert event.decision == AuditDecision.DENY
