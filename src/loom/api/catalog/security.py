import dataclasses
import uuid

import jwt
from jwt import PyJWKClient

from loom.config.auth_config import ServerAuthConfig
from loom.idp.catalog_roles import expand_claims_to_scopes  # noqa: F401
from loom.idp.discovery import (
    DiscoveryError,  # noqa: F401 -- re-exported: callers that fetch discovery themselves catch this
    OidcDiscoveryDocument,
    discover_oidc,
)
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


def discover_auth(auth: ServerAuthConfig) -> OidcDiscoveryDocument | None:
    """Fetch the OIDC discovery document at `auth.discovery_url` --
    the single source of the issuer, signing algorithms and endpoints
    `TokenValidator` and the Swagger flow depend on.

    Returns `None` when `discovery_url` is unset: auth is simply not
    configured yet (e.g. local/test runs that never intend to exercise it)
    -- not an error. Raises `DiscoveryError` immediately if the endpoint
    doesn't respond -- there is no valid degraded startup: every request
    needs a working `jwks_uri` to validate a single token. Called once, at
    server startup (`main.py`/`mcp/main.py`), so a bad discovery URL fails
    the process before it accepts any traffic, rather than surfacing as
    confusing per-request 401s later.
    """
    if not auth.discovery_url:
        return None
    return discover_oidc(auth.discovery_url)


class TokenValidator:
    """Validates bearer JWTs against a JWKS-published signing key."""

    def __init__(
        self, config: ServerAuthConfig, discovery: OidcDiscoveryDocument | None
    ) -> None:
        # `discovery` stays a required parameter (not defaulted) so every
        # call site is forced to think about where it comes from -- but its
        # *value* may legitimately be `None` (auth simply unconfigured, see
        # `discover_auth`), which is rejected here rather than attempting a
        # self-fetch against an empty URL. Every real call site already
        # resolves discovery once, up front, via `discover_auth` -- this
        # constructor never fetches.
        if discovery is None:
            raise ValueError(
                'TokenValidator requires a resolved OidcDiscoveryDocument -- '
                'auth.discovery_url must be configured. Build one via '
                '`discover_auth`, or pass one directly in tests.'
            )
        self._config = config
        self._issuer = discovery.issuer
        self._algorithms = list(discovery.signing_algorithms)
        self._jwk_client = PyJWKClient(
            discovery.jwks_uri, ssl_context=build_ssl_context()
        )

    def decode(self, token: str) -> dict:
        """Verify signature/exp/iss/aud and return the token's claims."""
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=self._algorithms,
            audience=self._config.audience,
            issuer=self._issuer,
        )


def assert_scopes(scopes: frozenset[str], *required: str) -> None:
    """Raise `InsufficientScopeError` unless every `required` scope is
    present in `scopes`."""
    missing = set(required) - scopes
    if missing:
        joined = ', '.join(sorted(missing))
        raise InsufficientScopeError(f'Missing required scope(s): {joined}')
