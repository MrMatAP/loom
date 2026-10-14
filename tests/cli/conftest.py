import pytest

from loom.idp.discovery import OidcDiscoveryDocument


@pytest.fixture(autouse=True)
def _fake_oidc_discovery(monkeypatch):
    """`loom-admin idp` commands read the issuer out of the IdP's discovery
    document -- fake the fetch rather than reaching `idp.example`."""
    monkeypatch.setattr(
        'loom.cli.idp.discover_oidc',
        lambda url: OidcDiscoveryDocument(
            issuer='https://idp.example/realms/loom',
            authorization_endpoint='https://idp.example/realms/loom/protocol/openid-connect/auth',
            token_endpoint='https://idp.example/realms/loom/protocol/openid-connect/token',
            jwks_uri='https://idp.example/realms/loom/protocol/openid-connect/certs',
        ),
    )
