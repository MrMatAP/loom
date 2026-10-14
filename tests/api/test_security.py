import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from loom.api.catalog import security as security_module
from loom.api.catalog.security import (
    OidcDiscoveryDocument,
    TokenValidator,
    discover_auth,
    expand_claims_to_scopes,
)
from loom.config import ApiServerConfig
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

    config = AuthConfig(audience='loom-catalog-api')
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
            'iss': discovery.issuer,
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
    network attempt against an empty URL."""
    config = AuthConfig()
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


def test_discover_auth_skips_when_unconfigured(tmp_path):
    """No `discovery_url` is "auth not configured yet", not an error --
    e.g. local/test runs."""
    config = ApiServerConfig(config_path=tmp_path / 'config.yaml')
    assert discover_auth(config.auth) is None


def test_discover_auth_fetches_the_configured_discovery_url(monkeypatch, tmp_path):
    captured = {}

    def _fake_discover(url):
        captured['url'] = url
        return _fake_discovery()

    monkeypatch.setattr(security_module, 'discover_oidc', _fake_discover)

    config = ApiServerConfig(config_path=tmp_path / 'config.yaml')
    config.auth.discovery_url = (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )

    discovery = discover_auth(config.auth)

    assert captured['url'] == config.auth.discovery_url
    assert discovery.issuer == 'https://idp.example/realms/loom'


def test_token_validator_rejects_a_token_from_another_issuer(monkeypatch):
    """The issuer is the discovery document's, not anything configured."""
    private_key, public_key = _make_rsa_keypair()
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk['kid'] = 'test-key'
    monkeypatch.setattr('jwt.PyJWKClient.fetch_data', lambda self: {'keys': [jwk]})
    validator = TokenValidator(AuthConfig(audience='a'), _fake_discovery())
    token = jwt.encode(
        {'iss': 'https://evil.example', 'aud': 'a', 'exp': int(time.time()) + 300},
        private_key,
        algorithm='RS256',
        headers={'kid': 'test-key'},
    )
    with pytest.raises(jwt.InvalidIssuerError):
        validator.decode(token)


def test_discover_auth_fails_immediately_when_unreachable(monkeypatch, tmp_path):
    """No response from the discovery endpoint must fail startup outright,
    not silently skip auth wiring."""

    def _raise(url):
        raise DiscoveryError(f'Could not reach {url}')

    monkeypatch.setattr(security_module, 'discover_oidc', _raise)

    config = ApiServerConfig(config_path=tmp_path / 'config.yaml')
    config.auth.discovery_url = (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )

    with pytest.raises(DiscoveryError):
        discover_auth(config.auth)
