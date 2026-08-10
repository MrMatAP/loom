import argparse

import pytest

from loom.cli.idp import idp_register_cli_client, idp_register_docs_client
from loom.config import RootConfig
from loom.idp.client import ClientRegistrationResult


class _FakeKeycloakAdminClient:
    def __init__(self, issuer, token):
        self.issuer = issuer
        self.token = token
        self.registered_public_client: dict | None = None
        self.audience_mapper: tuple[str, str] | None = None
        self.client_roles_mapper: tuple[str, str] | None = None
        self.tenant_id_mapper: str | None = None

    @classmethod
    async def login(cls, issuer, **kwargs):
        del kwargs
        return cls(issuer, 'fake-admin-token')

    async def register_public_client(self, *, client_id, client_name, **kwargs):
        self.registered_public_client = {
            'client_id': client_id,
            'client_name': client_name,
            **kwargs,
        }
        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref='fake-public-internal-id',
            registration_access_token=None,
            client_secret=None,
        )

    async def add_audience_mapper(self, client_ref, *, target_client_id):
        self.audience_mapper = (client_ref, target_client_id)

    async def add_client_roles_mapper(self, client_ref, *, source_client_id):
        self.client_roles_mapper = (client_ref, source_client_id)

    async def add_tenant_id_mapper(self, client_ref):
        self.tenant_id_mapper = client_ref


def _base_args(**overrides):
    defaults = {
        'issuer_url': 'https://idp.example/realms/loom',
        'admin_username': 'admin',
        'admin_password': 'hunter2',
        'admin_realm': 'master',
        'admin_client_id': 'admin-cli',
        'client_id': 'loom-catalog-docs',
        'client_name': None,
        'api_base_url': 'https://catalog.example.com',
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _configured_root_config(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.auth.audience = 'loom-catalog-api'
    return config


@pytest.mark.asyncio
async def test_register_docs_client_wires_standard_flow_and_redirect_uri(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    config = _configured_root_config(tmp_path)
    result = await idp_register_docs_client(config, _base_args())
    assert result == 0

    assert config.auth.docs_client_id == 'loom-catalog-docs'

    reloaded = RootConfig.load(config_path=config.config_path)
    assert reloaded.auth.docs_client_id == 'loom-catalog-docs'


@pytest.mark.asyncio
async def test_register_docs_client_requires_issuer(tmp_path):
    config = _configured_root_config(tmp_path)
    result = await idp_register_docs_client(config, _base_args(issuer_url=None))
    assert result == 1


@pytest.mark.asyncio
async def test_register_docs_client_requires_existing_audience(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register_docs_client(config, _base_args())
    assert result == 1
    assert config.auth.docs_client_id == ''


@pytest.mark.asyncio
async def test_register_docs_client_adds_audience_mapper_against_resource_server(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        async def register_public_client(self, *, client_id, client_name, **kwargs):
            captured['flow_kwargs'] = kwargs
            return await super().register_public_client(
                client_id=client_id, client_name=client_name, **kwargs
            )

        async def add_audience_mapper(self, client_ref, *, target_client_id):
            captured['audience_mapper'] = (client_ref, target_client_id)

        async def add_client_roles_mapper(self, client_ref, *, source_client_id):
            captured['client_roles_mapper'] = (client_ref, source_client_id)

        async def add_tenant_id_mapper(self, client_ref):
            captured['tenant_id_mapper'] = client_ref

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = _configured_root_config(tmp_path)
    await idp_register_docs_client(config, _base_args())

    assert captured['flow_kwargs']['standard_flow'] is True
    assert captured['flow_kwargs']['redirect_uris'] == (
        'https://catalog.example.com/docs/oauth2-redirect',
    )
    assert captured['flow_kwargs']['web_origins'] == ('https://catalog.example.com',)
    assert captured['audience_mapper'] == (
        'fake-public-internal-id',
        'loom-catalog-api',
    )
    assert captured['client_roles_mapper'] == (
        'fake-public-internal-id',
        'loom-catalog-api',
    )
    assert captured['tenant_id_mapper'] == 'fake-public-internal-id'


@pytest.mark.asyncio
async def test_register_docs_client_strips_trailing_slash_from_api_base_url(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        async def register_public_client(self, *, client_id, client_name, **kwargs):
            captured['flow_kwargs'] = kwargs
            return await super().register_public_client(
                client_id=client_id, client_name=client_name, **kwargs
            )

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = _configured_root_config(tmp_path)
    await idp_register_docs_client(
        config, _base_args(api_base_url='https://catalog.example.com/')
    )

    assert captured['flow_kwargs']['redirect_uris'] == (
        'https://catalog.example.com/docs/oauth2-redirect',
    )
    assert captured['flow_kwargs']['web_origins'] == ('https://catalog.example.com',)


@pytest.mark.asyncio
async def test_register_cli_client_wires_device_flow(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        async def register_public_client(self, *, client_id, client_name, **kwargs):
            captured['flow_kwargs'] = kwargs
            return await super().register_public_client(
                client_id=client_id, client_name=client_name, **kwargs
            )

        async def add_audience_mapper(self, client_ref, *, target_client_id):
            captured['audience_mapper'] = (client_ref, target_client_id)

        async def add_client_roles_mapper(self, client_ref, *, source_client_id):
            captured['client_roles_mapper'] = (client_ref, source_client_id)

        async def add_tenant_id_mapper(self, client_ref):
            captured['tenant_id_mapper'] = client_ref

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = _configured_root_config(tmp_path)
    result = await idp_register_cli_client(
        config, _base_args(client_id='loom-cli', client_name='Loom CLI')
    )
    assert result == 0

    assert captured['flow_kwargs']['device_flow'] is True
    assert captured['audience_mapper'] == (
        'fake-public-internal-id',
        'loom-catalog-api',
    )
    assert captured['client_roles_mapper'] == (
        'fake-public-internal-id',
        'loom-catalog-api',
    )
    assert captured['tenant_id_mapper'] == 'fake-public-internal-id'
    assert config.auth.cli_client_id == 'loom-cli'

    reloaded = RootConfig.load(config_path=config.config_path)
    assert reloaded.auth.cli_client_id == 'loom-cli'


@pytest.mark.asyncio
async def test_register_cli_client_requires_issuer(tmp_path):
    config = _configured_root_config(tmp_path)
    result = await idp_register_cli_client(
        config, _base_args(issuer_url=None, client_id='loom-cli')
    )
    assert result == 1


@pytest.mark.asyncio
async def test_register_cli_client_requires_existing_audience(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register_cli_client(config, _base_args(client_id='loom-cli'))
    assert result == 1
    assert config.auth.cli_client_id == ''
