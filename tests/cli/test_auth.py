import argparse
import time

import pydantic
import pytest

from loom.cli.auth import auth_login, auth_logout, auth_status
from loom.config import RootConfig
from loom.idp.device_flow import DeviceAuthorization, DeviceCodeError, DeviceTokens


def _base_args(**overrides):
    defaults = {'issuer_url': 'https://idp.example/realms/loom', 'client_id': None}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _configured_root_config(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.auth.cli_client_id = 'loom-cli'
    return config


class _FakeDeviceCodeClient:
    def __init__(self, issuer, client_id, **kwargs):
        del kwargs
        self.issuer = issuer
        self.client_id = client_id

    async def start(self):
        return DeviceAuthorization(
            device_code='devcode-123',
            user_code='ABCD-EFGH',
            verification_uri='https://idp.example/device',
            verification_uri_complete=(
                'https://idp.example/device?user_code=ABCD-EFGH'
            ),
            expires_in=600,
            interval=5,
        )

    async def poll(self, authorization):
        del authorization
        return DeviceTokens(
            access_token='access-tok', refresh_token='refresh-tok', expires_in=300
        )


class _DenyingDeviceCodeClient(_FakeDeviceCodeClient):
    async def poll(self, authorization):
        del authorization
        raise DeviceCodeError('Device login failed: access_denied')


@pytest.mark.asyncio
async def test_auth_login_caches_session_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)

    config = _configured_root_config(tmp_path)
    before = int(time.time())
    result = await auth_login(config, _base_args())
    assert result == 0

    assert config.auth.session.access_token.get_secret_value() == 'access-tok'
    assert config.auth.session.refresh_token.get_secret_value() == 'refresh-tok'
    assert config.auth.session.expires_at >= before + 300

    reloaded = RootConfig.load(config_path=config.config_path)
    assert reloaded.auth.session.access_token.get_secret_value() == 'access-tok'


@pytest.mark.asyncio
async def test_auth_login_requires_issuer(tmp_path):
    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args(issuer_url=None))
    assert result == 1


@pytest.mark.asyncio
async def test_auth_login_requires_client_id(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await auth_login(config, _base_args())
    assert result == 1


@pytest.mark.asyncio
async def test_auth_login_client_id_flag_beats_config(monkeypatch, tmp_path):
    captured = {}

    class _SpyDeviceCodeClient(_FakeDeviceCodeClient):
        def __init__(self, issuer, client_id, **kwargs):
            captured['client_id'] = client_id
            super().__init__(issuer, client_id, **kwargs)

    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _SpyDeviceCodeClient)

    config = _configured_root_config(tmp_path)
    await auth_login(config, _base_args(client_id='flag-client'))
    assert captured['client_id'] == 'flag-client'


@pytest.mark.asyncio
async def test_auth_login_reports_failure_without_caching_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _DenyingDeviceCodeClient)

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())
    assert result == 1
    assert config.auth.session.access_token is None


@pytest.mark.asyncio
async def test_auth_logout_clears_cached_session(tmp_path):
    config = _configured_root_config(tmp_path)
    config.auth.session.access_token = pydantic.SecretStr('access-tok')
    config.auth.session.refresh_token = pydantic.SecretStr('refresh-tok')
    config.auth.session.expires_at = int(time.time()) + 300
    config.save()

    result = await auth_logout(config, argparse.Namespace())
    assert result == 0
    assert config.auth.session.access_token is None
    assert config.auth.session.refresh_token is None
    assert config.auth.session.expires_at is None

    reloaded = RootConfig.load(config_path=config.config_path)
    assert reloaded.auth.session.access_token is None


@pytest.mark.asyncio
async def test_auth_status_reports_not_logged_in(tmp_path):
    config = _configured_root_config(tmp_path)
    result = await auth_status(config, argparse.Namespace())
    assert result == 1


@pytest.mark.asyncio
async def test_auth_status_reports_live_session(tmp_path):
    config = _configured_root_config(tmp_path)
    config.auth.session.access_token = pydantic.SecretStr('access-tok')
    config.auth.session.expires_at = int(time.time()) + 300

    result = await auth_status(config, argparse.Namespace())
    assert result == 0


@pytest.mark.asyncio
async def test_auth_status_reports_expired_session(tmp_path):
    config = _configured_root_config(tmp_path)
    config.auth.session.access_token = pydantic.SecretStr('access-tok')
    config.auth.session.expires_at = int(time.time()) - 10

    result = await auth_status(config, argparse.Namespace())
    assert result == 1
