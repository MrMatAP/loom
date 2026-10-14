import argparse

import pytest

from loom.cli import server_cmds
from loom.cli.server_cmds import _add_server_parser
from loom.config import AdminConfig, ApiServerConfig, McpServerConfig
from loom.persistence.roles import role_statements


@pytest.fixture
def admin(tmp_path) -> AdminConfig:
    admin = AdminConfig(config_path=tmp_path / 'admin.yaml')
    admin.database.host = 'db.internal'
    admin.auth.audience = 'loom-api'
    admin.auth.mcp_audience = 'loom-mcp'
    admin.auth.swagger_client_id = 'loom-api-swagger'
    admin.auth.discovery_url = 'https://idp.example/.well-known/openid-configuration'
    return admin


@pytest.fixture
def provisioned(monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        server_cmds, 'provision_role', lambda db, role, pw: calls.append((role, pw))
    )
    return calls


def _parse(server: str, *argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    _add_server_parser(parser.add_subparsers(), server)
    return parser.parse_args([server, *argv])


@pytest.mark.asyncio
async def test_api_config_creates_the_db_role_by_default(admin, tmp_path, provisioned):
    out = tmp_path / 'api.yaml'
    args = _parse('api', 'config', '--output', str(out), '--api-base-url', 'https://a')
    assert await args.func(admin, args) == 0

    config = ApiServerConfig.load(out)
    assert config.auth.audience == 'loom-api'
    assert config.api_base_url == 'https://a'
    ((role, password),) = provisioned
    assert role == 'loom_api'
    assert config.database.username == 'loom_api'
    assert config.database.password.get_secret_value() == password
    assert len(password) >= 32


@pytest.mark.asyncio
async def test_username_alone_still_creates_the_role_with_a_generated_password(
    admin, tmp_path, provisioned
):
    out = tmp_path / 'api.yaml'
    args = _parse('api', 'config', '--output', str(out), '--db-user', 'custom_api')
    assert await args.func(admin, args) == 0

    ((role, password),) = provisioned
    assert role == 'custom_api'
    config = ApiServerConfig.load(out)
    assert config.database.username == 'custom_api'
    assert config.database.password.get_secret_value() == password
    assert len(password) >= 32


@pytest.mark.asyncio
async def test_supplying_both_username_and_password_skips_role_creation(
    admin, tmp_path, provisioned
):
    out = tmp_path / 'mcp.yaml'
    args = _parse(
        'mcp', 'config', '--output', str(out),
        '--db-username', 'svc_mcp', '--db-password', 'pw',
    )  # fmt: skip
    assert await args.func(admin, args) == 0

    assert provisioned == []
    config = McpServerConfig.load(out)
    assert config.auth.audience == 'loom-mcp'
    assert config.database.username == 'svc_mcp'
    assert config.database.password.get_secret_value() == 'pw'


@pytest.mark.asyncio
async def test_db_role_prints_a_generated_password_once(admin, provisioned, capsys):
    args = _parse('api', 'db-role')
    assert await args.func(admin, args) == 0
    ((role, password),) = provisioned
    assert role == 'loom_api'
    assert password in capsys.readouterr().out


@pytest.mark.asyncio
async def test_db_role_does_not_echo_a_supplied_password(admin, provisioned, capsys):
    args = _parse('mcp', 'db-role', '--db-password', 'hunter2')
    assert await args.func(admin, args) == 0
    assert 'hunter2' not in capsys.readouterr().out


def test_role_statements_grant_dml_only_and_cover_future_tables():
    rendered = [
        s.as_string(None) for s in role_statements('loom_api', "p'w", 'loom', 'owner')
    ]
    joined = '\n'.join(rendered)
    assert "PASSWORD 'p''w'" in joined
    assert 'NOSUPERUSER NOCREATEDB NOCREATEROLE' in joined
    assert (
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public '
        'TO "loom_api"'
    ) in joined
    assert 'ALTER DEFAULT PRIVILEGES FOR ROLE "owner"' in joined
    for forbidden in (
        'ALL PRIVILEGES',
        'TRUNCATE',
        'REFERENCES',
        'TRIGGER',
        'OWNER TO',
    ):
        assert forbidden not in joined
    assert 'REVOKE CREATE ON SCHEMA public FROM "loom_api"' in joined


@pytest.mark.asyncio
@pytest.mark.parametrize('server', ['api', 'mcp'])
async def test_from_env_writes_the_servers_file(
    server, admin, tmp_path, monkeypatch, provisioned
):
    path = tmp_path / f'{server}.yaml'
    monkeypatch.setenv(f'LOOM_{server.upper()}_CONFIG_PATH', str(path))
    monkeypatch.setenv('LOOM_AUTH_AUDIENCE', 'loom-api')
    monkeypatch.setenv('LOOM_AUTH_MCP_AUDIENCE', 'loom-mcp')
    monkeypatch.setenv('LOOM_DB_HOST', 'db.example.com')

    args = _parse(server, 'from-env')
    assert await args.func(admin, args) == 0

    loaded = (ApiServerConfig if server == 'api' else McpServerConfig).load(path)
    assert loaded.auth.audience == ('loom-api' if server == 'api' else 'loom-mcp')
    assert loaded.database.host == 'db.example.com'
    assert provisioned == []
