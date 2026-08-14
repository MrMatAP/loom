import dataclasses
import uuid

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from loom.http_headers import TENANT_HINT_HEADER
from loom.model.enums import AuditDecision
from loom.model.governance import AuditEvent

from .db import flush_or_raise
from .dependencies import (
    get_current_token,
    get_session,
    parse_tenant_hint,
    resolve_principal,
)
from .security import AuthenticationError


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
    request: Request,
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> AuditActor:
    """Best-effort Principal resolution for audit attribution only --
    unlike `get_current_principal`, this never raises: a caller with no
    (or an ambiguous) provisioned Principal -- a platform administrator,
    most notably -- is a legitimate, expected case here, not an
    authentication failure. `require_scopes` is what actually gates access
    to the routes that use this. Delegates to the exact same
    `resolve_principal` (`X-Loom-Tenant-Id` header included) that gates
    access, so an event is never attributed to a Principal that
    authentication itself wouldn't have resolved to -- including staying
    unattributed (not guessing) when `external_id` resolves to more than
    one Principal with no header to disambiguate."""
    sub = claims.get('sub')
    principal_id: uuid.UUID | None = None
    try:
        tenant_hint = parse_tenant_hint(request.headers.get(TENANT_HINT_HEADER))
        principal = await resolve_principal(claims, session, tenant_hint=tenant_hint)
        principal_id = principal.principal_id
    except AuthenticationError:
        pass
    return AuditActor(principal_id=principal_id, external_id=sub)


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
