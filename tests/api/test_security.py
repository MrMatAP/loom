import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from loom.api.catalog import security as security_module
from loom.api.catalog.security import (
    IssuerMismatchError,
    OidcDiscoveryDocument,
    TokenValidator,
    discover_and_resolve_issuer,
    expand_claims_to_scopes,
)
from loom.config import RootConfig
from loom.config.auth_config import AuthConfig
from loom.idp.discovery import DiscoveryError


def _make_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def test_token_validator_decodes_valid_token(monkeypatch):
    private_key, public_key = _make_rsa_keypair()
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk['kid'] = 'test-key'
    jwk['alg'] = 'RS256'
    monkeypatch.setattr('jwt.PyJWKClient.fetch_data', lambda self: {'keys': [jwk]})

    config = AuthConfig(
        issuer='https://idp.example/realms/loom', audience='loom-catalog-api'
    )
    # Discovery passed in directly rather than pinning `config.discovery_url`
    # -- `TokenValidator` only reads `jwks_uri` off it, and passing it
    # explicitly (same as `main.py`'s lifespan does) skips the network
    # fetch a real discovery document would need here.
    discovery = OidcDiscoveryDocument(
        issuer='https://idp.example/realms/loom',
        authorization_endpoint='https://idp.example/realms/loom/protocol/openid-connect/auth',
        token_endpoint='https://idp.example/realms/loom/protocol/openid-connect/token',
        jwks_uri='https://idp.example/realms/loom/protocol/openid-connect/certs',
    )
    validator = TokenValidator(config, discovery)

    token = jwt.encode(
        {
            'sub': 'user-123',
            'tenant_id': str(uuid.uuid4()),
            'scope': 'catalog:agent:read',
            'iss': config.issuer,
            'aud': config.audience,
            'exp': int(time.time()) + 300,
        },
        private_key,
        algorithm='RS256',
        headers={'kid': 'test-key'},
    )

    claims = validator.decode(token)
    assert claims['sub'] == 'user-123'


def test_expand_claims_to_scopes_merges_scope_and_role_claims():
    scopes = expand_claims_to_scopes(
        {'scope': 'catalog:agent:read', 'roles': ['catalog-viewer']}
    )
    assert 'catalog:agent:read' in scopes
    assert 'catalog:capability:read' in scopes


def test_token_validator_rejects_no_discovery():
    """No self-fetch fallback anymore -- a caller that can't supply a
    resolved `OidcDiscoveryDocument` gets a clear error, not a broken-URL
    network attempt against an empty issuer."""
    config = AuthConfig(issuer='https://idp.example/realms/loom')
    with pytest.raises(ValueError, match='requires a resolved OidcDiscoveryDocument'):
        TokenValidator(config, None)


def _fake_discovery(**overrides) -> OidcDiscoveryDocument:
    defaults = {
        'issuer': 'https://idp.example/realms/loom',
        'authorization_endpoint': (
            'https://idp.example/realms/loom/protocol/openid-connect/auth'
        ),
        'token_endpoint': 'https://idp.example/realms/loom/protocol/openid-connect/token',
        'jwks_uri': 'https://idp.example/realms/loom/protocol/openid-connect/certs',
    }
    defaults.update(overrides)
    return OidcDiscoveryDocument(**defaults)


def test_discover_and_resolve_issuer_skips_when_entirely_unconfigured(tmp_path):
    """Neither `discovery_url` nor `issuer` set is "auth not configured
    yet", not an error -- e.g. local/test runs."""
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    assert discover_and_resolve_issuer(config) is None


def test_discover_and_resolve_issuer_populates_and_persists_an_unset_issuer(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(security_module, 'discover_oidc', lambda url: _fake_discovery())

    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    config.auth.discovery_url = (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )

    discovery = discover_and_resolve_issuer(config)

    assert discovery.issuer == 'https://idp.example/realms/loom'
    assert config.auth.issuer == 'https://idp.example/realms/loom'
    reloaded = RootConfig.load(config_path=config_path)
    assert reloaded.auth.issuer == 'https://idp.example/realms/loom'


def test_discover_and_resolve_issuer_derives_discovery_url_from_a_stored_issuer(
    monkeypatch, tmp_path
):
    """A legacy config with only `issuer` set (no `discovery_url`) still
    works -- the discovery URL is derived from it, same as before this
    became the primary stored value."""
    captured = {}

    def _fake_discover(url):
        captured['url'] = url
        return _fake_discovery()

    monkeypatch.setattr(security_module, 'discover_oidc', _fake_discover)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.auth.issuer = 'https://idp.example/realms/loom'

    discover_and_resolve_issuer(config)

    assert captured['url'] == (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )


def test_discover_and_resolve_issuer_raises_on_mismatch(monkeypatch, tmp_path):
    """A stored issuer that disagrees with what discovery now reports must
    stop startup -- `issuer` is the trust anchor `TokenValidator.decode`
    checks every token's `iss` claim against."""
    monkeypatch.setattr(security_module, 'discover_oidc', lambda url: _fake_discovery())

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.auth.discovery_url = (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )
    config.auth.issuer = 'https://old-idp.example/realms/old'

    with pytest.raises(IssuerMismatchError, match='does not match'):
        discover_and_resolve_issuer(config)


def test_discover_and_resolve_issuer_fails_immediately_when_unreachable(
    monkeypatch, tmp_path
):
    """No response from the discovery endpoint must fail startup outright,
    not silently skip auth wiring."""

    def _raise(url):
        raise DiscoveryError(f'Could not reach {url}')

    monkeypatch.setattr(security_module, 'discover_oidc', _raise)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.auth.discovery_url = (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )

    with pytest.raises(DiscoveryError):
        discover_and_resolve_issuer(config)
