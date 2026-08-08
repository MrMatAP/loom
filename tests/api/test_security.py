import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from loom.api.catalog.security import TokenValidator, expand_claims_to_scopes
from loom.config.auth_config import AuthConfig


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
        issuer='https://idp.example/realms/loom',
        audience='loom-catalog-api',
        jwks_uri='https://idp.example/realms/loom/protocol/openid-connect/certs',
    )
    validator = TokenValidator(config)

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
