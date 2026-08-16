import dataclasses
import uuid

import jwt
from jwt import PyJWKClient

from loom.config import RootConfig
from loom.config.auth_config import AuthConfig
from loom.idp.catalog_roles import expand_claims_to_scopes  # noqa: F401
from loom.idp.discovery import (
    DiscoveryError,  # noqa: F401 -- re-exported: callers that fetch discovery themselves catch this
    OidcDiscoveryDocument,
    default_discovery_url,
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


class IssuerMismatchError(RuntimeError):
    """Raised by `discover_and_resolve_issuer` when a *stored* `auth.issuer`
    disagrees with the issuer published by its own configured discovery
    document. `issuer` is the trust anchor `TokenValidator.decode` checks
    every token's `iss` claim against -- a stale or wrong value here is a
    security-relevant misconfiguration, so this must stop the server from
    starting rather than silently trusting either value."""


def discover_and_resolve_issuer(config: RootConfig) -> OidcDiscoveryDocument | None:
    """Fetch `config.auth`'s OIDC discovery document and reconcile
    `config.auth.issuer` against it. `auth.discovery_url` is the primary,
    stored value going forward (`loom idp register` populates it
    alongside `issuer` -- see `cli/idp.py`); `issuer` is *derived* from it
    on demand:

    - Neither `discovery_url` nor `issuer` set: returns `None`. Auth is
      simply not configured yet (e.g. local/test runs that never intend to
      exercise it) -- not an error.
    - `issuer` unset: populated from the discovery document's own
      `issuer` field and persisted (`config.save()`) into whatever file
      `config.config_path` names -- for a single long-lived process/host
      that means later startups skip straight to the "agrees" case below.
      Under Kubernetes, `$LOOM_CONFIG_PATH` typically lives in a per-pod
      `emptyDir` (see docs/admin-guide.md's "Configuration" section), so
      each pod still re-derives and re-saves it once on its own first
      startup -- one extra discovery-document read per pod, not per
      request, which is the only cost this is actually saving.
    - `issuer` set and it disagrees with discovery: raises
      `IssuerMismatchError` -- see that class's docstring. Reconfigure it
      with `loom config set auth.issuer <value>` (or clear it to `''` to
      have it re-derived next startup) once you know which is correct.
    - `issuer` set and it agrees: no-op, returns the fetched document.

    Raises `DiscoveryError` immediately if the discovery endpoint doesn't
    respond -- there is no valid degraded startup: every request needs a
    working `jwks_uri` to validate a single token. Called once, at server
    startup (`main.py`/`mcp/main.py`), so a bad discovery/issuer fails the
    process before it accepts any traffic, rather than surfacing as
    confusing per-request 401s later.
    """
    auth = config.auth
    if not auth.discovery_url and not auth.issuer:
        return None
    discovery_url = auth.discovery_url or default_discovery_url(auth.issuer)
    discovery = discover_oidc(discovery_url)
    if not auth.issuer:
        auth.issuer = discovery.issuer
        config.save()
    elif auth.issuer != discovery.issuer:
        raise IssuerMismatchError(
            f'Configured auth.issuer ({auth.issuer!r}) does not match the '
            f'issuer published by its own OIDC discovery document at '
            f'{discovery_url!r} ({discovery.issuer!r}). Refusing to start '
            'with an inconsistent trust anchor.\n'
            f'Reconfigure it once you know which is correct:\n'
            f'    loom config set auth.issuer {discovery.issuer}\n'
            'or clear it to accept whatever discovery reports next startup:\n'
            "    loom config set auth.issuer ''"
        )
    return discovery


class TokenValidator:
    """Validates bearer JWTs against a JWKS-published signing key."""

    def __init__(
        self, config: AuthConfig, discovery: OidcDiscoveryDocument | None
    ) -> None:
        # `discovery` stays a required parameter (not defaulted) so every
        # call site is forced to think about where it comes from -- but its
        # *value* may legitimately be `None` (auth simply unconfigured, see
        # `discover_and_resolve_issuer`), which is rejected here rather
        # than attempting a self-fetch against an empty issuer: there used
        # to be a fallback that did that, and it produced a confusing
        # broken-URL error instead of this clear one. Every real call site
        # already resolves discovery once, up front, via
        # `discover_and_resolve_issuer` -- this constructor never fetches.
        if discovery is None:
            raise ValueError(
                'TokenValidator requires a resolved OidcDiscoveryDocument -- '
                'auth.issuer/auth.discovery_url must be configured. Build one '
                'via `discover_and_resolve_issuer`, or pass one directly in '
                'tests.'
            )
        self._config = config
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
