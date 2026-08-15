import dataclasses
import uuid

import httpx
import jwt
from jwt import PyJWKClient

from loom.config.auth_config import AuthConfig
from loom.idp.catalog_roles import expand_claims_to_scopes  # noqa: F401
from loom.tls import build_ssl_context


@dataclasses.dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The resolved identity and permissions behind a validated request."""

    principal_id: uuid.UUID
    tenant_id: uuid.UUID
    scopes: frozenset[str]


class AuthenticationError(Exception):
    """Raised when a token can't be resolved to an internal Principal.

    Transport-neutral: the REST dependency chain (`dependencies.py`) and
    the MCP tool adapter (`mcp/server.py`) both raise this from the same
    underlying resolution logic and translate it into their own wire
    format (HTTPException(401) vs. a plain error message back to the
    calling agent).
    """


class InsufficientScopeError(Exception):
    """Raised when a principal's scopes don't cover a required set. Same
    transport-neutral split as `AuthenticationError`."""


class PrincipalNotInTenantError(Exception):
    """Raised when a token resolves to a real identity (`sub`), but that
    identity has no Principal in the specific Tenant the request targets
    (`tenant_id`, from the URL path -- see `dependencies.get_current_
    principal`). Deliberately distinct from `AuthenticationError`: the
    token itself is fine, so re-authenticating won't fix this, which is
    exactly why it maps to 403, not 401 -- same reasoning as
    `InsufficientScopeError`, just gated on tenant membership instead of
    scope. Same transport-neutral split as the other two."""


@dataclasses.dataclass(frozen=True)
class OidcDiscoveryDocument:
    """The subset of an IdP's OIDC discovery document
    (`.well-known/openid-configuration`) this service depends on."""

    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


def default_discovery_url(issuer: str) -> str:
    """The spec-defined discovery document location for an OIDC issuer
    (OpenID Connect Discovery 1.0), used when `AuthConfig.discovery_url`
    isn't pinned to something else."""
    return f'{issuer.rstrip("/")}/.well-known/openid-configuration'


def discover_oidc(discovery_url: str) -> OidcDiscoveryDocument:
    """Fetch and parse the IdP's own discovery document -- the spec-defined
    source for these endpoints, rather than assuming a particular IdP's URL
    conventions (e.g. Keycloak's `.../protocol/openid-connect/{auth,token}`
    layout, which doesn't generalize to every OIDC-compliant IdP)."""
    ctx = build_ssl_context()
    response = httpx.get(discovery_url, timeout=10.0, verify=ctx)
    response.raise_for_status()
    doc = response.json()
    return OidcDiscoveryDocument(
        authorization_endpoint=doc['authorization_endpoint'],
        token_endpoint=doc['token_endpoint'],
        jwks_uri=doc['jwks_uri'],
    )


class TokenValidator:
    """Validates bearer JWTs against a JWKS-published signing key."""

    def __init__(
        self, config: AuthConfig, discovery: OidcDiscoveryDocument | None = None
    ) -> None:
        self._config = config
        if discovery is None:
            discovery_url = config.discovery_url or default_discovery_url(config.issuer)
            discovery = discover_oidc(discovery_url)
        self._jwk_client = PyJWKClient(
            discovery.jwks_uri, ssl_context=build_ssl_context()
        )

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


def assert_scopes(scopes: frozenset[str], *required: str) -> None:
    """Raise `InsufficientScopeError` unless every `required` scope is
    present in `scopes`."""
    missing = set(required) - scopes
    if missing:
        joined = ', '.join(sorted(missing))
        raise InsufficientScopeError(f'Missing required scope(s): {joined}')
