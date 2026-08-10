import argparse

import pytest

from loom.cli.idp import idp_register_client
from loom.config import RootConfig
from loom.idp.client import ClientRegistrationResult


class _FakeKeycloakAdminClient:
    def __init__(self, issuer, token):
        self.issuer = issuer
        self.token = token
        self.declared_roles = None

    @classmethod
    async def login(cls, issuer, **kwargs):
        del kwargs
        return cls(issuer, 'fake-admin-token')

    async def register_client(self, *, client_id, client_name, service_account):
        del client_name, service_account
        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref='fake-internal-id',
            registration_access_token=None,
            client_secret='fake-secret',
        )

    async def declare_client_roles(self, client_ref, roles):
        self.declared_roles = (client_ref, roles)


def _base_args(**overrides):
    defaults = {
        'issuer_url': 'https://idp.example/realms/loom',
        'admin_username': 'admin',
        'admin_password': 'hunter2',
        'admin_realm': 'master',
        'admin_client_id': 'admin-cli',
        'client_id': 'loom-catalog-api',
        'client_name': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.mark.asyncio
async def test_idp_register_client_wires_arguments(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register_client(config, _base_args())
    assert result == 0

    output = capsys.readouterr().out
    assert 'loom-catalog-api' in output
    assert 'fake-secret' in output
    assert 'Declared 32 roles' in output


@pytest.mark.asyncio
async def test_idp_register_client_requires_issuer(tmp_path):
    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    result = await idp_register_client(config, _base_args(issuer_url=None))
    assert result == 1
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_idp_register_client_updates_and_saves_auth_config(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    result = await idp_register_client(config, _base_args())
    assert result == 0

    assert config.auth.issuer == 'https://idp.example/realms/loom'
    assert config.auth.audience == 'loom-catalog-api'
    assert config.auth.jwks_uri is None

    reloaded = RootConfig.load(config_path=config_path)
    assert reloaded.auth.issuer == 'https://idp.example/realms/loom'
    assert reloaded.auth.audience == 'loom-catalog-api'
    assert reloaded.auth.jwks_uri is None

    output = capsys.readouterr().out
    assert 'Updated local config' in output
    assert 'auth.issuer=https://idp.example/realms/loom' in output
    assert 'auth.audience=loom-catalog-api' in output


@pytest.mark.asyncio
async def test_idp_register_client_overwrites_existing_auth_config(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.auth.issuer = 'https://old-idp.example/realms/old'
    config.auth.audience = 'old-client-id'

    await idp_register_client(config, _base_args())

    assert config.auth.issuer == 'https://idp.example/realms/loom'
    assert config.auth.audience == 'loom-catalog-api'


@pytest.mark.asyncio
async def test_idp_register_client_resets_stale_jwks_uri(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    # Seed a config file on disk carrying a stale jwks_uri, mimicking an
    # operator who previously pinned it against a different issuer/proxy.
    config_path = tmp_path / 'config.yaml'
    seed = RootConfig(config_path=config_path)
    seed.auth.jwks_uri = (
        'https://old-idp.example/realms/old/protocol/openid-connect/certs'
    )
    seed.save()

    config = RootConfig.load(config_path=config_path)
    assert config.auth.jwks_uri is not None

    await idp_register_client(config, _base_args())

    assert config.auth.jwks_uri is None

    # If the on-disk file were never rewritten, this reload would still
    # carry the seeded stale value rather than falling back to the default.
    reloaded = RootConfig.load(config_path=config_path)
    assert reloaded.auth.jwks_uri is None


@pytest.mark.asyncio
async def test_admin_username_env_var_fallback(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del password
            captured['username'] = username
            captured['admin_realm'] = kwargs['admin_realm']
            captured['admin_client_id'] = kwargs['admin_client_id']
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.setenv('LOOM_IDP_ADMIN_USERNAME', 'env-admin')
    monkeypatch.delenv('LOOM_IDP_ADMIN_PASSWORD', raising=False)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_username=None))
    assert captured['username'] == 'env-admin'
    assert captured['admin_realm'] == 'master'
    assert captured['admin_client_id'] == 'admin-cli'


@pytest.mark.asyncio
async def test_admin_password_prompted_when_flag_and_env_both_absent(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del username
            captured['password'] = password
            captured['admin_realm'] = kwargs['admin_realm']
            captured['admin_client_id'] = kwargs['admin_client_id']
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.delenv('LOOM_IDP_ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr('loom.cli.idp.getpass.getpass', lambda prompt: 'prompted-pw')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_password=None))
    assert captured['password'] == 'prompted-pw'
    assert captured['admin_realm'] == 'master'
    assert captured['admin_client_id'] == 'admin-cli'


@pytest.mark.asyncio
async def test_admin_username_flag_beats_env(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del password, kwargs
            captured['username'] = username
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.setenv('LOOM_IDP_ADMIN_USERNAME', 'env-admin')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_username='flag-admin'))
    assert captured['username'] == 'flag-admin'


@pytest.mark.asyncio
async def test_admin_password_flag_beats_env(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del username, kwargs
            captured['password'] = password
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.setenv('LOOM_IDP_ADMIN_PASSWORD', 'env-pw')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_password='flag-pw'))
    assert captured['password'] == 'flag-pw'


@pytest.mark.asyncio
async def test_idp_register_client_wires_admin_realm_and_client_id(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del username, password
            captured['admin_realm'] = kwargs['admin_realm']
            captured['admin_client_id'] = kwargs['admin_client_id']
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args())

    assert captured['admin_realm'] == 'master'
    assert captured['admin_client_id'] == 'admin-cli'


@pytest.mark.asyncio
async def test_idp_register_client_wires_custom_admin_realm_and_client_id(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del username, password
            captured['admin_realm'] = kwargs['admin_realm']
            captured['admin_client_id'] = kwargs['admin_client_id']
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(
        config,
        _base_args(admin_realm='internal-admins', admin_client_id='custom-admin-cli'),
    )

    assert captured['admin_realm'] == 'internal-admins'
    assert captured['admin_client_id'] == 'custom-admin-cli'
