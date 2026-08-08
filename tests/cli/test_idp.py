import argparse

import pytest

from loom.cli.idp import idp_register_client
from loom.config import RootConfig
from loom.idp.client import ClientRegistrationResult


class _FakeKeycloakAdminClient:
    def __init__(self, issuer, token, **kwargs):
        del kwargs
        self.issuer = issuer
        self.token = token
        self.declared_roles = None

    async def register_client(self, *, client_id, client_name, service_account):
        del client_name, service_account
        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref='fake-internal-id',
            registration_access_token='fake-rat',
        )

    async def declare_client_roles(self, client_ref, roles):
        self.declared_roles = (client_ref, roles)


@pytest.mark.asyncio
async def test_idp_register_client_wires_arguments(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    args = argparse.Namespace(
        issuer_url='https://idp.example/realms/loom',
        token='initial-access-token',
        client_id='loom-catalog-api',
        client_name=None,
    )

    result = await idp_register_client(config, args)
    assert result == 0

    output = capsys.readouterr().out
    assert 'loom-catalog-api' in output
    assert 'fake-rat' in output
    assert 'Declared 29 roles' in output


@pytest.mark.asyncio
async def test_idp_register_client_requires_issuer(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    args = argparse.Namespace(
        issuer_url=None, token='t', client_id='x', client_name=None
    )
    result = await idp_register_client(config, args)
    assert result == 1
