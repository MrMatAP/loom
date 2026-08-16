import dataclasses

import httpx

from loom.tls import build_ssl_context


class DiscoveryError(RuntimeError):
    """Raised when an IdP's OIDC discovery document can't be fetched, or
    doesn't contain what a caller needs from it. Deliberately not caught
    anywhere near where it's raised -- every caller (server startup, `loom
    auth login`) needs a real, working discovery document to do anything
    useful, so there is no valid degraded mode to fall back to."""


@dataclasses.dataclass(frozen=True)
class OidcDiscoveryDocument:
    """The subset of an IdP's OIDC discovery document
    (`.well-known/openid-configuration`) Loom depends on -- shared by the
    server (issuer/JWKS validation, `api/catalog/security.py`) and every
    interactive client (device-flow endpoints: the `loom` CLI here via
    `idp/device_flow.py`, and the VS Code/IntelliJ plugins via their own
    discovery fetch)."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    # Defined by RFC 8628 as an optional discovery metadata extension --
    # Keycloak has published it since v12, but it's not guaranteed by every
    # IdP's discovery document. Callers that need the Device Authorization
    # Grant must handle its absence themselves (see `cli/auth.py`).
    device_authorization_endpoint: str | None = None


def default_discovery_url(issuer: str) -> str:
    """The spec-defined discovery document location for an OIDC issuer
    (OpenID Connect Discovery 1.0) -- used to locate a discovery document
    when only a (previously-stored) issuer is known, e.g. an `auth.issuer`
    set before `auth.discovery_url` was. Once resolved, `discovery_url`
    stays the primary, stored value going forward; this is a bootstrap
    convenience, not a fallback the steady-state code depends on."""
    return f'{issuer.rstrip("/")}/.well-known/openid-configuration'


def discover_oidc(discovery_url: str) -> OidcDiscoveryDocument:
    """Fetch and parse the IdP's own discovery document -- the spec-defined
    source for these endpoints and the issuer itself, rather than assuming
    a particular IdP's URL conventions (e.g. Keycloak's
    `.../protocol/openid-connect/{auth,token}` layout, which doesn't
    generalize to every OIDC-compliant IdP). Raises `DiscoveryError`
    immediately on any failure -- unreachable, non-2xx, malformed body, or
    missing a required field -- rather than returning a partial document."""
    ctx = build_ssl_context()
    try:
        response = httpx.get(discovery_url, timeout=10.0, verify=ctx)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DiscoveryError(
            f'Could not reach the OIDC discovery endpoint at {discovery_url}: {exc}'
        ) from exc
    try:
        doc = response.json()
    except ValueError as exc:
        raise DiscoveryError(
            f'{discovery_url} did not return valid JSON: {exc}'
        ) from exc
    try:
        return OidcDiscoveryDocument(
            issuer=doc['issuer'],
            authorization_endpoint=doc['authorization_endpoint'],
            token_endpoint=doc['token_endpoint'],
            jwks_uri=doc['jwks_uri'],
            device_authorization_endpoint=doc.get('device_authorization_endpoint'),
        )
    except KeyError as exc:
        raise DiscoveryError(
            f'{discovery_url} is missing required field {exc}'
        ) from exc
