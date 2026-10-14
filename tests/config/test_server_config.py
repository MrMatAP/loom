from pydantic import SecretStr

from loom.config import AdminConfig, ApiServerConfig, McpServerConfig


def test_from_env_maps_the_documented_variables(tmp_path):
    environ = {
        'LOOM_DB_HOST': 'db.example.com',
        'LOOM_DB_PORT': '6432',
        'LOOM_DB_PASSWORD': 's3cret',
        'LOOM_AUTH_DISCOVERY_URL': 'https://idp.example/realms/loom/.well-known/openid-configuration',
        'LOOM_AUTH_AUDIENCE': 'loom-catalog-api',
        'LOOM_CATALOG_API_BASE_URL': 'https://catalog.example.com',
    }
    path = tmp_path / 'server.yaml'

    config = ApiServerConfig.from_env(path, environ)
    config.save()
    reloaded = ApiServerConfig.load(path)

    assert reloaded.database.host == 'db.example.com'
    assert reloaded.database.port == 6432
    assert reloaded.database.password.get_secret_value() == 's3cret'
    assert reloaded.auth.audience == 'loom-catalog-api'
    assert reloaded.auth.discovery_url == (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )
    assert reloaded.api_base_url == 'https://catalog.example.com'
    assert reloaded.auth.cli_client_id == 'loom-cli'


def test_from_env_with_nothing_set_yields_defaults(tmp_path):
    config = ApiServerConfig.from_env(tmp_path / 'server.yaml', {})
    assert config.database.host == 'localhost'
    assert config.api_base_url is None


def test_mcp_from_env_takes_its_audience_from_the_mcp_variable(tmp_path):
    environ = {
        'LOOM_AUTH_AUDIENCE': 'loom-catalog-api',
        'LOOM_AUTH_MCP_AUDIENCE': 'loom-catalog-api-mcp',
        'LOOM_AUTH_DISCOVERY_URL': 'https://idp.example/.well-known/openid-configuration',
        'LOOM_DB_HOST': 'db.example.com',
    }
    config = McpServerConfig.from_env(tmp_path / 'mcp.yaml', environ)
    assert config.auth.audience == 'loom-catalog-api-mcp'
    assert config.auth.discovery_url == environ['LOOM_AUTH_DISCOVERY_URL']
    assert config.database.host == 'db.example.com'


def _registered_admin(tmp_path) -> AdminConfig:
    admin = AdminConfig(config_path=tmp_path / 'admin.yaml')
    admin.database.host = 'db.internal'
    admin.database.username = 'migrator'
    admin.database.password = SecretStr('migrator-pw')
    admin.auth.audience = 'loom-api'
    admin.auth.mcp_audience = 'loom-mcp'
    admin.auth.swagger_client_id = 'loom-api-swagger'
    admin.auth.cli_client_id = 'loom-cli-x'
    admin.auth.discovery_url = 'https://idp.example/.well-known/openid-configuration'
    return admin


def test_api_from_admin_uses_the_api_client_ids(tmp_path):
    config = ApiServerConfig.from_admin(
        _registered_admin(tmp_path),
        tmp_path / 'api.yaml',
        api_base_url='https://api.example.com/',
    )
    assert config.auth.audience == 'loom-api'
    assert config.auth.swagger_client_id == 'loom-api-swagger'
    assert config.auth.cli_client_id == 'loom-cli-x'
    assert config.auth.discovery_url.endswith('openid-configuration')
    assert config.api_base_url == 'https://api.example.com'
    assert config.database.host == 'db.internal'
    assert config.database.username != 'migrator'
    assert config.database.password is None


def test_mcp_from_admin_uses_the_mcp_client_id(tmp_path):
    config = McpServerConfig.from_admin(
        _registered_admin(tmp_path), tmp_path / 'mcp.yaml'
    )
    assert config.auth.audience == 'loom-mcp'
    assert config.auth.discovery_url.endswith('openid-configuration')
    assert not hasattr(config.auth, 'swagger_client_id')
    assert config.database.host == 'db.internal'
    assert config.database.password is None
