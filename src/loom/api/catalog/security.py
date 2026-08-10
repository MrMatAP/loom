import dataclasses
import uuid

import httpx
import jwt
from jwt import PyJWKClient

from loom.config.auth_config import AuthConfig
from loom.idp.catalog_roles import ROLE_BUNDLES
from loom.tls import build_ssl_context


@dataclasses.dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The resolved identity and permissions behind a validated request."""

    principal_id: uuid.UUID
    tenant_id: uuid.UUID
    scopes: frozenset[str]


def resolve_jwks_uri(issuer: str) -> str:
    """Discover the JWKS URI from the issuer's OIDC discovery document."""
    ctx = build_ssl_context()
    response = httpx.get(
        f'{issuer}/.well-known/openid-configuration', timeout=10.0, verify=ctx
    )
    response.raise_for_status()
    return response.json()['jwks_uri']


def default_authorization_endpoint(issuer: str) -> str:
    """The Keycloak-conventional authorization endpoint for an issuer."""
    return f'{issuer.rstrip("/")}/protocol/openid-connect/auth'


def default_token_endpoint(issuer: str) -> str:
    """The Keycloak-conventional token endpoint for an issuer."""
    return f'{issuer.rstrip("/")}/protocol/openid-connect/token'


class TokenValidator:
    """Validates bearer JWTs against a JWKS-published signing key."""

    def __init__(self, config: AuthConfig) -> None:
        self._config = config
        jwks_uri = config.jwks_uri or resolve_jwks_uri(config.issuer)
        self._jwk_client = PyJWKClient(jwks_uri, ssl_context=build_ssl_context())

    def decode(self, token: str) -> dict:
        """Verify signature/exp/iss/aud and return the token's claims."""
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=self._config.algorithms,
            audience=self._config.audience,
            issuer=self._config.issuer,
        )


def expand_claims_to_scopes(claims: dict) -> frozenset[str]:
    """Expand a token's scope/roles claims into a flat scope set."""
    scopes: set[str] = set()
    scope_claim = claims.get('scope')
    if scope_claim:
        scopes.update(scope_claim.split())
    for role in claims.get('roles', []):
        scopes.update(ROLE_BUNDLES.get(role, {role}))
    return frozenset(scopes)
