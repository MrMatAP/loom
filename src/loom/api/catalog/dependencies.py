import uuid
from collections.abc import AsyncGenerator

import jwt
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2AuthorizationCodeBearer
from sqlalchemy.ext.asyncio import AsyncSession

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


async def resolve_principal(
    claims: dict, session: AsyncSession
) -> AuthenticatedPrincipal:
    """Resolve a decoded token's tenant_id/sub claims to an internal
    Principal. Transport-neutral: shared by `get_current_principal` below
    and the MCP tool adapter's own auth path (`mcp/server.py`), which
    doesn't have FastAPI's `Depends()` machinery to reach this through."""
    tenant_claim = claims.get('tenant_id')
    sub = claims.get('sub')
    if not tenant_claim or not sub:
        raise AuthenticationError('Token missing tenant_id or sub claim')
    try:
        tenant_id = uuid.UUID(str(tenant_claim))
    except ValueError as exc:
        raise AuthenticationError('Token tenant_id claim is not a UUID') from exc
    principal = await session.scalar(
        sa.select(Principal).where(
            Principal.tenant_id == tenant_id, Principal.external_id == sub
        )
    )
    if principal is None:
        raise AuthenticationError('No principal provisioned for this identity')
    return AuthenticatedPrincipal(
        principal_id=principal.id,
        tenant_id=tenant_id,
        scopes=expand_claims_to_scopes(claims),
    )


async def get_current_principal(
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedPrincipal:
    """Resolve the token's tenant_id/sub claims to an internal Principal."""
    try:
        return await resolve_principal(claims, session)
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
