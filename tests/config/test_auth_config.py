from loom.config.auth_config import AuthConfig
from loom.config.root_config import RootConfig


def test_auth_config_defaults():
    config = AuthConfig(
        issuer='https://idp.example/realms/loom', audience='loom-catalog-api'
    )
    assert config.algorithms == ['RS256']
    assert config.jwks_uri is None


def test_root_config_has_auth_section(tmp_path):
    root = RootConfig(config_path=tmp_path / 'config.yaml')
    assert root.auth.algorithms == ['RS256']
