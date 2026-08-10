"""Tier 1: needs only LOOM_IDP_ISSUER_URL, no admin credentials.

Proves the live issuer is reachable over the TLS trust configured via
`loom.tls.build_ssl_context` (see README's "CA trust" note), and that the
Keycloak-conventional endpoint derivation in `security.py` matches what
this specific instance actually advertises.
"""

import pytest

from loom.api.catalog.security import (
    default_authorization_endpoint,
    default_token_endpoint,
)

from .live import ISSUER_ENV_VAR, requires_env_vars

pytestmark = [pytest.mark.live_idp, requires_env_vars(ISSUER_ENV_VAR)]


def test_discovery_document_is_reachable(live_issuer, live_discovery_document):
    assert live_discovery_document['issuer'] == live_issuer


def test_default_endpoint_helpers_match_the_real_discovery_document(
    live_issuer, live_discovery_document
):
    assert (
        default_authorization_endpoint(live_issuer)
        == live_discovery_document['authorization_endpoint']
    )
    assert (
        default_token_endpoint(live_issuer) == live_discovery_document['token_endpoint']
    )


def test_discovery_document_advertises_the_device_code_grant(live_discovery_document):
    """The `loom` CLI's device flow (device_flow.py) has nowhere to point
    without both of these -- catches an instance where device flow is
    disabled realm-wide before any client registration is attempted."""
    assert 'device_authorization_endpoint' in live_discovery_document
    assert (
        'urn:ietf:params:oauth:grant-type:device_code'
        in live_discovery_document['grant_types_supported']
    )
