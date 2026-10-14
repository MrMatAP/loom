from loom.config import ApiServerConfig
from loom.config.auth_config import AuthConfig


def test_auth_config_defaults():
    config = AuthConfig(audience='loom-catalog-api')
    assert not hasattr(config, 'issuer')
    assert not hasattr(config, 'algorithms')
    assert config.discovery_url is None


def test_root_config_has_auth_section(tmp_path):
    root = ApiServerConfig(config_path=tmp_path / 'config.yaml')
    assert root.auth.discovery_url is None
