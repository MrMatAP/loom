import dataclasses
import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.domain.enums import AuditDecision
from loom.persistence.governance import AuditEvent

from .db import flush_or_raise
from .dependencies import get_current_token, get_principal_in_tenant, get_session
from .security import AuthenticationError, PrincipalNotInTenantError


@dataclasses.dataclass(frozen=True)
class AuditActor:
    """Who performed an action, for `AuditEvent.actor_principal_id`
    attribution. A platform administrator (see docs/admin-guide.md) has no
    Principal row -- `principal_id` is None for them, and `external_id`
    (the token's `sub`) is what keeps the audit trail attributable to a
    real IDP identity even without one."""

    principal_id: uuid.UUID | None
    external_id: str | None


async def get_audit_actor(
    tenant_id: uuid.UUID,
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> AuditActor:
    """Best-effort Principal resolution for audit attribution only,
    scoped to the Tenant in the request's own URL path -- unlike
    `get_current_principal`, this never raises: a caller with no
    Principal in that Tenant yet -- a platform administrator provisioning
    the first one, most notably -- is a legitimate, expected case here,
    not an authentication failure. `require_scopes` is what actually gates
    access to the routes that use this. Delegates to the exact same
    `get_principal_in_tenant` that gates access, so an event is never
    attributed to a Principal that authentication itself wouldn't have
    resolved to.

    Not for `POST /tenants` itself -- that route has no Tenant in its own
    path (it's creating one); see `get_audit_actor_for_new_tenant`."""
    sub = claims.get('sub')
    principal_id: uuid.UUID | None = None
    try:
        principal = await get_principal_in_tenant(tenant_id, claims, session)
        principal_id = principal.principal_id
    except AuthenticationError, PrincipalNotInTenantError:
        pass
    return AuditActor(principal_id=principal_id, external_id=sub)


async def get_audit_actor_for_new_tenant(
    claims: dict = Depends(get_current_token),
) -> AuditActor:
    """Audit attribution for `POST /tenants` specifically -- the one
    route with no Tenant of its own in the URL path to check Principal
    membership against (it's the one being created), so unlike
    `get_audit_actor` there's nothing to resolve: always attributed via
    `details.actor_external_id` only. Creating a Tenant is inherently a
    cross-Tenant, platform-admin action; a Principal row in some other,
    unrelated Tenant wouldn't be a meaningful attribution here anyway."""
    return AuditActor(principal_id=None, external_id=claims.get('sub'))


async def record_audit_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor: AuditActor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
) -> None:
    """Write one immutable AuditEvent row. `details` carries the actor's
    `sub` whenever there's no Principal row to attribute the event to
    directly (see `AuditActor`), so that identity isn't lost entirely."""
    details = {} if actor.principal_id else {'actor_external_id': actor.external_id}
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_principal_id=actor.principal_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            decision=AuditDecision.ALLOW,
            details=details,
        )
    )
    await flush_or_raise(session)
