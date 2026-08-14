import uuid
from collections.abc import AsyncGenerator

import jwt
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2AuthorizationCodeBearer
from sqlalchemy.ext.asyncio import AsyncSession

from loom.http_headers import TENANT_HINT_HEADER
from loom.model.tenant import Principal

from .security import (
    AuthenticatedPrincipal,
    AuthenticationError,
    InsufficientScopeError,
    assert_scopes,
    expand_claims_to_scopes,
)

# Authorization/token URLs are placeholders filled in by create_app() from the
# running config, since this scheme is a module-level singleton shared by
# every route's dependency tree (see main.py). Request-time token extraction
# doesn't depend on those URLs at all -- only the OpenAPI doc / Swagger UI's
# interactive login flow does.
oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl='',
    tokenUrl='',
    scopes={'openid': 'OpenID Connect', 'profile': 'Basic profile information'},
    auto_error=True,
)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """Yield a request-scoped async session, committing on success."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_token(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> dict:
    """Validate the bearer token and return its decoded claims."""
    validator = request.app.state.token_validator
    try:
        return validator.decode(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f'Invalid token: {exc}') from exc


async def principals_for_external_id(
    session: AsyncSession, external_id: str
) -> list[Principal]:
    """Every Principal row for a token's `sub` claim -- tokens no longer
    carry a `tenant_id` claim at all (see docs/admin-guide.md's "Platform
    administrator" section), so `Principal.tenant_id` is now the sole
    source of which Tenant a caller belongs to, and this lookup is by
    `external_id` alone. Shared by `resolve_principal` below (which needs
    to distinguish "none" from "more than one" for its error message) and
    `get_audit_actor` in `audit.py` (which only needs "exactly one, or
    give up") -- both must resolve identity the same way, or an event
    could get attributed differently than it was authenticated."""
    rows = await session.scalars(
        sa.select(Principal).where(Principal.external_id == external_id)
    )
    return list(rows)


def parse_tenant_hint(raw: str | None) -> uuid.UUID | None:
    """Parse the `X-Loom-Tenant-Id` disambiguation header (see `loom auth
    set-tenant`) into a UUID, or None if the header wasn't sent. Raises
    `AuthenticationError` (not a bare `ValueError`) rather than silently
    ignoring a malformed value, so both the REST dependency chain and the
    MCP tool adapter can catch it alongside every other identity-resolution
    failure."""
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise AuthenticationError(
            f'{TENANT_HINT_HEADER} header is not a valid UUID'
        ) from exc


async def resolve_principal(
    claims: dict, session: AsyncSession, *, tenant_hint: uuid.UUID | None = None
) -> AuthenticatedPrincipal:
    """Resolve a decoded token's `sub` claim to an internal Principal.
    Transport-neutral: shared by `get_current_principal` below and the MCP
    tool adapter's own auth path (`mcp/server.py`), which doesn't have
    FastAPI's `Depends()` machinery to reach this through.

    `tenant_hint` disambiguates when `sub` resolves to more than one
    Principal -- the same identity legitimately provisioned in more than
    one Tenant (`external_id` is only unique per Tenant, not globally; see
    `src/loom/model/tenant.py`'s `Principal` docstring). Ignored when
    there's only one match, so it's always safe to pass regardless of
    whether the caller's identity is actually ambiguous."""
    sub = claims.get('sub')
    if not isinstance(sub, str) or not sub:
        raise AuthenticationError('Token missing or malformed sub claim')
    principals = await principals_for_external_id(session, sub)
    if not principals:
        raise AuthenticationError('No principal provisioned for this identity')
    if len(principals) == 1:
        principal = principals[0]
    elif tenant_hint is None:
        candidates = ', '.join(str(p.tenant_id) for p in principals)
        raise AuthenticationError(
            'Identity provisioned in multiple tenants; ambiguous -- run '
            f'`loom auth set-tenant <tenant_id>` to choose one (candidates: '
            f'{candidates})'
        )
    else:
        matches = [p for p in principals if p.tenant_id == tenant_hint]
        if not matches:
            raise AuthenticationError(
                f'No principal provisioned for this identity in tenant {tenant_hint}'
            )
        principal = matches[0]
    return AuthenticatedPrincipal(
        principal_id=principal.id,
        tenant_id=principal.tenant_id,
        scopes=expand_claims_to_scopes(claims),
    )


async def get_current_principal(
    request: Request,
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedPrincipal:
    """Resolve the token's `sub` claim (plus the `X-Loom-Tenant-Id` header,
    if sent -- see `parse_tenant_hint`) to an internal Principal."""
    try:
        tenant_hint = parse_tenant_hint(request.headers.get(TENANT_HINT_HEADER))
        return await resolve_principal(claims, session, tenant_hint=tenant_hint)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_scopes(*required: str):
    """Dependency factory: 403s unless the token's scopes cover all required."""

    async def _check(claims: dict = Depends(get_current_token)) -> None:
        scopes = expand_claims_to_scopes(claims)
        try:
            assert_scopes(scopes, *required)
        except InsufficientScopeError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return _check
