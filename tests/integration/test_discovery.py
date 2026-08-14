"""Tier 1: needs only LOOM_IDP_ISSUER_URL, no admin credentials.

Proves the live issuer is reachable over the TLS trust configured via
`loom.tls.build_ssl_context` (see README's "CA trust" note), and that its
discovery document actually advertises the keys `security.py`'s
`discover_oidc` indexes -- the real KeyError-at-startup guard, now that
`authorization_endpoint`/`token_endpoint`/`jwks_uri` are read straight out
of the document instead of guessed from a URL convention.
"""

import pytest

from .live import ISSUER_ENV_VAR, requires_env_vars

pytestmark = [pytest.mark.live_idp, requires_env_vars(ISSUER_ENV_VAR)]


def test_discovery_document_is_reachable(live_issuer, live_discovery_document):
    assert live_discovery_document['issuer'] == live_issuer


def test_discovery_document_advertises_the_endpoints_this_service_reads(
    live_discovery_document,
):
    for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
        assert live_discovery_document[key]


def test_discovery_document_advertises_the_device_code_grant(live_discovery_document):
    """The `loom` CLI's device flow (device_flow.py) has nowhere to point
    without both of these -- catches an instance where device flow is
    disabled realm-wide before any client registration is attempted."""
    assert 'device_authorization_endpoint' in live_discovery_document
    assert (
        'urn:ietf:params:oauth:grant-type:device_code'
        in live_discovery_document['grant_types_supported']
    )
