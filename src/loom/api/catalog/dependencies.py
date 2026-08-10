import uuid
from collections.abc import AsyncGenerator

import jwt
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2AuthorizationCodeBearer
from sqlalchemy.ext.asyncio import AsyncSession

from loom.model.tenant import Principal

from .security import AuthenticatedPrincipal, expand_claims_to_scopes

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


async def get_current_principal(
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedPrincipal:
    """Resolve the token's tenant_id/sub claims to an internal Principal."""
    tenant_claim = claims.get('tenant_id')
    sub = claims.get('sub')
    if not tenant_claim or not sub:
        detail = 'Token missing tenant_id or sub claim'
        raise HTTPException(status_code=401, detail=detail)
    try:
        tenant_id = uuid.UUID(str(tenant_claim))
    except ValueError as exc:
        detail = 'Token tenant_id claim is not a UUID'
        raise HTTPException(status_code=401, detail=detail) from exc
    principal = await session.scalar(
        sa.select(Principal).where(
            Principal.tenant_id == tenant_id, Principal.external_id == sub
        )
    )
    if principal is None:
        detail = 'No principal provisioned for this identity'
        raise HTTPException(status_code=401, detail=detail)
    return AuthenticatedPrincipal(
        principal_id=principal.id,
        tenant_id=tenant_id,
        scopes=expand_claims_to_scopes(claims),
    )


def require_scopes(*required: str):
    """Dependency factory: 403s unless the token's scopes cover all required."""

    async def _check(claims: dict = Depends(get_current_token)) -> None:
        scopes = expand_claims_to_scopes(claims)
        missing = set(required) - scopes
        if missing:
            joined = ', '.join(sorted(missing))
            detail = f'Missing required scope(s): {joined}'
            raise HTTPException(status_code=403, detail=detail)

    return _check
