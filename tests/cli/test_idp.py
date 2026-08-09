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
    assert 'Declared 29 roles' in output


@pytest.mark.asyncio
async def test_idp_register_client_requires_issuer(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register_client(config, _base_args(issuer_url=None))
    assert result == 1


@pytest.mark.asyncio
async def test_admin_username_env_var_fallback(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del kwargs
            captured['username'] = username
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.setenv('LOOM_IDP_ADMIN_USERNAME', 'env-admin')
    monkeypatch.delenv('LOOM_IDP_ADMIN_PASSWORD', raising=False)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_username=None))
    assert captured['username'] == 'env-admin'


@pytest.mark.asyncio
async def test_admin_password_prompted_when_flag_and_env_both_absent(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del kwargs
            captured['password'] = password
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.delenv('LOOM_IDP_ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr('loom.cli.idp.getpass.getpass', lambda prompt: 'prompted-pw')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_password=None))
    assert captured['password'] == 'prompted-pw'
