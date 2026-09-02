import uuid
from collections.abc import AsyncGenerator

import jwt
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2AuthorizationCodeBearer
from sqlalchemy.ext.asyncio import AsyncSession

from loom.persistence.tenant import Principal

from .security import (
    AuthenticatedPrincipal,
    AuthenticationError,
    InsufficientScopeError,
    PrincipalNotInTenantError,
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
    """Every Principal row for a token's `sub` claim, across every Tenant
    -- unlike `get_principal_in_tenant` below (which checks membership in
    one specific, already-known Tenant), this is for the one place that
    still needs the full cross-Tenant set: `GET /tenants/mine`
    (`tenant/router.py`), which lists every Tenant a caller has a
    Principal in so the CLI can offer a choice *before* one has been
    picked."""
    rows = await session.scalars(
        sa.select(Principal).where(Principal.external_id == external_id)
    )
    return list(rows)


async def get_principal_in_tenant(
    tenant_id: uuid.UUID, claims: dict, session: AsyncSession
) -> AuthenticatedPrincipal:
    """Resolve a decoded token's `sub` claim to the Principal it holds in
    `tenant_id` specifically. Transport-neutral: shared by
    `get_current_principal` below and the MCP tool adapter's own auth path
    (`mcp/server.py`), which doesn't have FastAPI's `Depends()` machinery
    to reach this through.

    `tenant_id` is mandatory and always comes from the request's own URL
    path -- every router but Tenant's own nests under
    `/tenants/{tenant_id}/...`. An identity legitimately provisioned in
    more than one Tenant (`external_id` is only unique per Tenant, not
    globally; see `src/loom/model/tenant.py`'s `Principal` docstring)
    targets a specific one that way, by putting it in the URL, rather than
    through an ambiguous/hint-disambiguated lookup --
    `uq_principal_tenant_external_id` guarantees at most one row can
    match."""
    sub = claims.get('sub')
    if not isinstance(sub, str) or not sub:
        raise AuthenticationError('Token missing or malformed sub claim')
    principal = await session.scalar(
        sa.select(Principal).where(
            Principal.external_id == sub, Principal.tenant_id == tenant_id
        )
    )
    if principal is None:
        raise PrincipalNotInTenantError(
            f'No principal provisioned for this identity in tenant {tenant_id}'
        )
    return AuthenticatedPrincipal(
        principal_id=principal.id,
        tenant_id=tenant_id,
        scopes=expand_claims_to_scopes(claims),
    )


async def get_current_principal(
    tenant_id: uuid.UUID,
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedPrincipal:
    """Resolve the token's `sub` claim to the Principal it holds in the
    Tenant named by the request's own URL path (`tenant_id`) -- FastAPI
    fills this in the same way it fills any other path parameter, since
    every router using this dependency is mounted under
    `/tenants/{tenant_id}/...`."""
    try:
        return await get_principal_in_tenant(tenant_id, claims, session)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PrincipalNotInTenantError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def require_scopes(*required: str):
    """Dependency factory: 403s unless the token's scopes cover all required."""

    async def _check(claims: dict = Depends(get_current_token)) -> None:
        scopes = expand_claims_to_scopes(claims)
        try:
            assert_scopes(scopes, *required)
        except InsufficientScopeError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return _check
