import argparse

import pytest

from loom.cli.idp import idp_register
from loom.config import RootConfig

from .fake_keycloak import FakeKeycloakAdminClient


def _base_args(**overrides):
    defaults = {
        'issuer_url': 'https://idp.example/realms/loom',
        'admin_username': 'admin',
        'admin_password': 'hunter2',
        'admin_realm': 'master',
        'admin_client_id': 'admin-cli',
        'client_id': 'loom-catalog-api',
        'client_name': None,
        'api_base_url': 'https://catalog.example.com',
        'mcp_client_id': None,
        'mcp_client_name': None,
        'swagger_client_id': None,
        'swagger_client_name': None,
        'cli_client_id': None,
        'cli_client_name': None,
        'access_token_lifespan': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.mark.asyncio
async def test_swagger_client_derives_its_default_id_from_client_id(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    assert config.auth.swagger_client_id == 'loom-catalog-api-swagger'


@pytest.mark.asyncio
async def test_swagger_client_id_override_is_honored(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(swagger_client_id='custom-swagger'))

    assert config.auth.swagger_client_id == 'custom-swagger'


@pytest.mark.asyncio
async def test_swagger_client_wires_standard_flow_and_redirect_uri(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
        async def register_public_client(self, *, client_id, client_name, **kwargs):
            if client_id.endswith('-swagger'):
                captured['flow_kwargs'] = kwargs
            return await super().register_public_client(
                client_id=client_id, client_name=client_name, **kwargs
            )

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    assert captured['flow_kwargs']['standard_flow'] is True
    assert captured['flow_kwargs']['redirect_uris'] == (
        'https://catalog.example.com/docs/oauth2-redirect',
    )
    assert captured['flow_kwargs']['web_origins'] == ('https://catalog.example.com',)


@pytest.mark.asyncio
async def test_swagger_client_strips_trailing_slash_from_api_base_url(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
        async def register_public_client(self, *, client_id, client_name, **kwargs):
            if client_id.endswith('-swagger'):
                captured['flow_kwargs'] = kwargs
            return await super().register_public_client(
                client_id=client_id, client_name=client_name, **kwargs
            )

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(api_base_url='https://catalog.example.com/'))

    assert captured['flow_kwargs']['redirect_uris'] == (
        'https://catalog.example.com/docs/oauth2-redirect',
    )
    assert captured['flow_kwargs']['web_origins'] == ('https://catalog.example.com',)


@pytest.mark.asyncio
async def test_cli_client_derives_its_default_id_from_client_id(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    assert config.auth.cli_client_id == 'loom-catalog-api-cli'


@pytest.mark.asyncio
async def test_cli_client_id_override_is_honored(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(cli_client_id='loom-cli'))

    assert config.auth.cli_client_id == 'loom-cli'

    reloaded = RootConfig.load(config_path=config.config_path)
    assert reloaded.auth.cli_client_id == 'loom-cli'


@pytest.mark.asyncio
async def test_cli_client_wires_device_flow(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
        async def register_public_client(self, *, client_id, client_name, **kwargs):
            if client_id.endswith('-cli'):
                captured['flow_kwargs'] = kwargs
            return await super().register_public_client(
                client_id=client_id, client_name=client_name, **kwargs
            )

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register(config, _base_args())
    assert result == 0

    assert captured['flow_kwargs']['device_flow'] is True


@pytest.mark.asyncio
async def test_mcp_client_derives_its_default_id_and_sets_mcp_audience(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    assert config.auth.mcp_audience == 'loom-catalog-api-mcp'


@pytest.mark.asyncio
async def test_mcp_client_id_override_is_honored(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(mcp_client_id='loom-mcp'))

    assert config.auth.mcp_audience == 'loom-mcp'


@pytest.mark.asyncio
async def test_public_clients_each_get_two_audience_mappers_one_per_resource_server(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    captured: dict[str, FakeKeycloakAdminClient] = {}

    class _CapturingKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, **kwargs):
            del kwargs
            instance = cls(issuer, 'fake-admin-token')
            captured['client'] = instance
            return instance

    monkeypatch.setattr(
        'loom.cli.idp.KeycloakAdminClient', _CapturingKeycloakAdminClient
    )

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    fake = captured['client']
    api_client_id = 'loom-catalog-api'
    mcp_client_id = 'loom-catalog-api-mcp'
    swagger_ref = FakeKeycloakAdminClient._internal_ref('loom-catalog-api-swagger')
    cli_ref = FakeKeycloakAdminClient._internal_ref('loom-catalog-api-cli')

    assert set(fake.audience_mappers) == {
        (swagger_ref, api_client_id),
        (swagger_ref, mcp_client_id),
        (cli_ref, api_client_id),
        (cli_ref, mcp_client_id),
    }

    # `client-roles` mappers only ever reference the API client -- the MCP
    # client carries no roles of its own to flatten (see
    # `idp._register_mcp_client`).
    assert fake.client_roles_mappers == [
        (swagger_ref, api_client_id),
        (cli_ref, api_client_id),
    ]


@pytest.mark.asyncio
async def test_leaves_lifespan_alone_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv('LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN', raising=False)
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    captured: dict[str, FakeKeycloakAdminClient] = {}

    class _CapturingKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, **kwargs):
            del kwargs
            instance = cls(issuer, 'fake-admin-token')
            captured['client'] = instance
            return instance

    monkeypatch.setattr(
        'loom.cli.idp.KeycloakAdminClient', _CapturingKeycloakAdminClient
    )

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    assert captured['client'].access_token_lifespan is None


@pytest.mark.asyncio
async def test_applies_access_token_lifespan_flag(monkeypatch, tmp_path):
    captured: dict[str, FakeKeycloakAdminClient] = {}

    class _CapturingKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, **kwargs):
            del kwargs
            instance = cls(issuer, 'fake-admin-token')
            captured['client'] = instance
            return instance

    monkeypatch.setattr(
        'loom.cli.idp.KeycloakAdminClient', _CapturingKeycloakAdminClient
    )

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(access_token_lifespan=1800))

    cli_ref = FakeKeycloakAdminClient._internal_ref('loom-catalog-api-cli')
    assert captured['client'].access_token_lifespan == (cli_ref, 1800)


@pytest.mark.asyncio
async def test_applies_access_token_lifespan_env_var(monkeypatch, tmp_path):
    captured: dict[str, FakeKeycloakAdminClient] = {}

    class _CapturingKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, **kwargs):
            del kwargs
            instance = cls(issuer, 'fake-admin-token')
            captured['client'] = instance
            return instance

    monkeypatch.setattr(
        'loom.cli.idp.KeycloakAdminClient', _CapturingKeycloakAdminClient
    )
    monkeypatch.setenv('LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN', '1800')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    cli_ref = FakeKeycloakAdminClient._internal_ref('loom-catalog-api-cli')
    assert captured['client'].access_token_lifespan == (cli_ref, 1800)


@pytest.mark.asyncio
async def test_access_token_lifespan_flag_takes_precedence_over_env_var(
    monkeypatch, tmp_path
):
    captured: dict[str, FakeKeycloakAdminClient] = {}

    class _CapturingKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, **kwargs):
            del kwargs
            instance = cls(issuer, 'fake-admin-token')
            captured['client'] = instance
            return instance

    monkeypatch.setattr(
        'loom.cli.idp.KeycloakAdminClient', _CapturingKeycloakAdminClient
    )
    monkeypatch.setenv('LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN', '1800')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(access_token_lifespan=3600))

    cli_ref = FakeKeycloakAdminClient._internal_ref('loom-catalog-api-cli')
    assert captured['client'].access_token_lifespan == (cli_ref, 3600)
