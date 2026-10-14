import argparse
import time
import typing
import uuid

import httpx
import jwt
import pydantic
import pytest

from loom.catalog_client import CatalogApiError
from loom.cli.auth import (
    auth_login,
    auth_logout,
    auth_set_tenant,
    auth_status,
    auth_whoami,
)
from loom.config import UserConfig
from loom.config.auth_config import DEFAULT_CLI_CLIENT_ID
from loom.idp.device_flow import DeviceAuthorization, DeviceCodeError, DeviceTokens
from loom.idp.discovery import DiscoveryError, OidcDiscoveryDocument

_DISCOVERY_URL = 'https://idp.example/realms/loom/.well-known/openid-configuration'


def _token_with_claims(**claims: typing.Any) -> str:
    return jwt.encode(claims, 'test-secret', algorithm='HS256')


def _base_args(**overrides):
    defaults = {'discovery_url': _DISCOVERY_URL, 'client_id': None, 'server': None}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _configured_root_config(tmp_path):
    config = UserConfig(config_path=tmp_path / 'config.yaml')
    return config


def _fake_discovery(**overrides) -> OidcDiscoveryDocument:
    defaults = {
        'issuer': 'https://idp.example/realms/loom',
        'authorization_endpoint': (
            'https://idp.example/realms/loom/protocol/openid-connect/auth'
        ),
        'token_endpoint': 'https://idp.example/realms/loom/protocol/openid-connect/token',
        'jwks_uri': 'https://idp.example/realms/loom/protocol/openid-connect/certs',
        'device_authorization_endpoint': (
            'https://idp.example/realms/loom/protocol/openid-connect/auth/device'
        ),
    }
    defaults.update(overrides)
    return OidcDiscoveryDocument(**defaults)


@pytest.fixture(autouse=True)
def _patch_discovery(monkeypatch):
    """Every `auth_login` test below goes through `discover_oidc` now --
    default to a working fake so only the tests specifically about
    discovery failure need to override this."""
    monkeypatch.setattr(
        'loom.cli.auth.discover_oidc', lambda discovery_url: _fake_discovery()
    )


class _FakeDeviceCodeClient:
    def __init__(self, endpoints, client_id, **kwargs):
        del kwargs
        self.endpoints = endpoints
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


def _fake_catalog_client(*, items=(), error: Exception | None = None):
    """Stands in for `CatalogClient` in `auth_login` tests -- avoids a real
    network call to `config.catalog.api_base_url` (which may point at a
    real local dev server) for tests that aren't specifically exercising
    the post-login tenant-selection step itself. `items` mirrors the
    `Page[TenantRead]` shape `GET /tenants/mine` actually returns."""

    class _Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def get(self, path, params=None):
            del path, params
            if error is not None:
                raise error
            return {'items': list(items), 'total': len(items), 'limit': 50, 'offset': 0}

    return _Client


@pytest.mark.asyncio
async def test_auth_login_caches_session_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr('loom.cli.auth.CatalogClient', _fake_catalog_client())

    config = _configured_root_config(tmp_path)
    before = int(time.time())
    result = await auth_login(config, _base_args())
    assert result == 0

    assert config.auth.session.access_token.get_secret_value() == 'access-tok'
    assert config.auth.session.refresh_token.get_secret_value() == 'refresh-tok'
    assert config.auth.session.expires_at >= before + 300

    reloaded = UserConfig.load(config_path=config.config_path)
    assert reloaded.auth.session.access_token.get_secret_value() == 'access-tok'


@pytest.mark.asyncio
async def test_auth_login_clears_a_stale_tenant_selection(monkeypatch, tmp_path):
    """A fresh login can resolve to a different identity than whichever one
    last ran `loom auth set-tenant` -- e.g. the common expired-session path
    is `loom auth login` again, with no intervening `logout`. A selection
    left over from the old identity must not silently carry forward onto
    the new one."""
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr('loom.cli.auth.CatalogClient', _fake_catalog_client())

    config = _configured_root_config(tmp_path)
    config.auth.session.tenant_id = uuid.uuid4()
    config.save()

    result = await auth_login(config, _base_args())
    assert result == 0
    assert config.auth.session.tenant_id is None


@pytest.mark.asyncio
async def test_auth_login_auto_selects_the_only_available_tenant(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(
            items=[{'id': str(tenant_id), 'slug': 'default', 'name': 'Default'}]
        ),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert config.auth.session.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_auth_login_prompts_when_multiple_tenants_are_available(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(
            items=[
                {'id': str(first_id), 'slug': 'first', 'name': 'First'},
                {'id': str(second_id), 'slug': 'second', 'name': 'Second'},
            ]
        ),
    )
    monkeypatch.setattr('builtins.input', lambda _prompt: '2')

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert config.auth.session.tenant_id == second_id


@pytest.mark.asyncio
async def test_auth_login_reprompts_on_an_invalid_selection(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(
            items=[
                {'id': str(first_id), 'slug': 'first', 'name': 'First'},
                {'id': str(second_id), 'slug': 'second', 'name': 'Second'},
            ]
        ),
    )
    answers = iter(['nope', '99', '1'])
    monkeypatch.setattr('builtins.input', lambda _prompt: next(answers))

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert config.auth.session.tenant_id == first_id


@pytest.mark.asyncio
async def test_auth_login_gives_up_selection_on_eof_without_failing_login(
    monkeypatch, tmp_path
):
    """Declining to choose (e.g. a non-interactive `loom auth login` in a
    script) must not throw away the tokens that were already obtained --
    only `set-tenant` needs to be run later."""
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(
            items=[
                {'id': str(uuid.uuid4()), 'slug': 'first', 'name': 'First'},
                {'id': str(uuid.uuid4()), 'slug': 'second', 'name': 'Second'},
            ]
        ),
    )

    def _raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr('builtins.input', _raise_eof)

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert config.auth.session.access_token is not None
    assert config.auth.session.tenant_id is None


@pytest.mark.asyncio
async def test_auth_login_reports_no_tenant_available_without_failing(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr('loom.cli.auth.CatalogClient', _fake_catalog_client(items=[]))

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert config.auth.session.tenant_id is None


@pytest.mark.asyncio
async def test_auth_login_fails_on_a_tenant_listing_api_error(monkeypatch, tmp_path):
    """Unable to even list Tenants is a login failure -- unlike "zero
    Tenants available" or "declined to choose among several" (both
    legitimate, non-error outcomes), this means the identity's usability
    couldn't be determined at all. Tokens are still cached (see below) so
    a caller isn't forced to redo the device-code flow just to retry
    tenant selection -- only the command's exit code changes."""
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(error=CatalogApiError(503, 'Service Unavailable')),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 1
    assert config.auth.session.access_token is not None
    assert config.auth.session.tenant_id is None


@pytest.mark.asyncio
async def test_auth_login_fails_on_a_tenant_listing_connection_error(
    monkeypatch, tmp_path
):
    """Same as the API-error case above, but for a transport-level failure
    (unreachable `catalog.api_base_url`, DNS, etc.) rather than an HTTP
    error response -- `CatalogClient` doesn't wrap these as
    `CatalogApiError`, so `_select_tenant` must catch them too."""
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(error=httpx.ConnectError('Connection refused')),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 1
    assert config.auth.session.access_token is not None
    assert config.auth.session.tenant_id is None


def _fake_catalog_client_recording_posts(post_calls: list):
    class _Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def get(self, path, params=None):
            del path, params
            tenants = [{'id': str(uuid.uuid4()), 'slug': 'default', 'name': 'Default'}]
            return {'items': tenants, 'total': 1, 'limit': 50, 'offset': 0}

        async def post(self, path, payload):
            post_calls.append((path, payload))
            return {}

    return _Client


@pytest.mark.asyncio
async def test_auth_login_never_registers_a_principal(monkeypatch, tmp_path):
    """Creating Principals is an explicit Platform Operator action
    (`loom principal create`), not a side effect of logging in -- even for
    an identity holding `platform:principal:write`."""
    token = _token_with_claims(sub='operator', scope='platform:principal:write')

    class _Client(_FakeDeviceCodeClient):
        async def poll(self, authorization):
            del authorization
            return DeviceTokens(
                access_token=token, refresh_token='refresh-tok', expires_in=300
            )

    post_calls: list = []
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _Client)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient', _fake_catalog_client_recording_posts(post_calls)
    )
    monkeypatch.setattr(
        'loom.cli.auth.discover_oidc', lambda discovery_url: _fake_discovery()
    )

    result = await auth_login(_configured_root_config(tmp_path), _base_args())

    assert result == 0
    assert post_calls == []


@pytest.mark.asyncio
async def test_auth_login_requires_discovery_url(tmp_path):
    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args(discovery_url=None))
    assert result == 1


@pytest.mark.asyncio
async def test_auth_login_fails_immediately_when_discovery_is_unreachable(
    monkeypatch, tmp_path
):
    """No degraded mode -- a discovery endpoint that doesn't respond must
    fail the login outright, not fall back to guessing Keycloak's
    conventional endpoint paths."""

    def _raise(discovery_url):
        raise DiscoveryError(
            f'Could not reach the OIDC discovery endpoint at {discovery_url}'
        )

    monkeypatch.setattr('loom.cli.auth.discover_oidc', _raise)

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())
    assert result == 1
    assert config.auth.session.access_token is None


@pytest.mark.asyncio
async def test_auth_login_fails_when_discovery_lacks_device_authorization_endpoint(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        'loom.cli.auth.discover_oidc',
        lambda discovery_url: _fake_discovery(device_authorization_endpoint=None),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())
    assert result == 1
    assert config.auth.session.access_token is None


@pytest.mark.asyncio
async def test_auth_login_client_id_flag_beats_config(monkeypatch, tmp_path):
    captured = {}

    class _SpyDeviceCodeClient(_FakeDeviceCodeClient):
        def __init__(self, endpoints, client_id, **kwargs):
            captured['client_id'] = client_id
            super().__init__(endpoints, client_id, **kwargs)

    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _SpyDeviceCodeClient)
    monkeypatch.setattr('loom.cli.auth.CatalogClient', _fake_catalog_client())

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
    config.auth.session.tenant_id = uuid.uuid4()
    config.save()

    result = await auth_logout(config, argparse.Namespace())
    assert result == 0
    assert config.auth.session.access_token is None
    assert config.auth.session.refresh_token is None
    assert config.auth.session.expires_at is None
    assert config.auth.session.tenant_id is None

    reloaded = UserConfig.load(config_path=config.config_path)
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


@pytest.mark.asyncio
async def test_auth_whoami_reports_not_logged_in(tmp_path):
    config = _configured_root_config(tmp_path)
    result = await auth_whoami(config, argparse.Namespace())
    assert result == 1


@pytest.mark.asyncio
async def test_auth_whoami_prints_headline_and_other_claims(tmp_path, capsys):
    config = _configured_root_config(tmp_path)
    config.auth.session.access_token = pydantic.SecretStr(
        _token_with_claims(
            sub='user-123',
            name='Ada Lovelace',
            scope='email profile',
            roles=['catalog:capability:read'],
            iss='https://idp.example/realms/loom',
            aud=['loom-api'],
            azp='loom-cli',
            jti='some-jwt-id',
        )
    )

    result = await auth_whoami(config, argparse.Namespace())
    out = capsys.readouterr().out

    assert result == 0
    assert 'user-123' in out
    assert 'Ada Lovelace' in out
    assert 'catalog:capability:read' in out
    # Non-headline claims (jti) still show up, just under "other claims".
    assert 'jti' in out
    assert 'some-jwt-id' in out


@pytest.mark.asyncio
async def test_auth_whoami_does_not_verify_the_signature(tmp_path):
    """Deliberately shows the user their own cached token back to
    themselves without a JWKS fetch -- must work even against a token an
    unrelated key signed, since it's not a trust decision."""
    config = _configured_root_config(tmp_path)
    config.auth.session.access_token = pydantic.SecretStr(
        jwt.encode({'sub': 'user-123'}, 'a-completely-different-key', algorithm='HS256')
    )

    result = await auth_whoami(config, argparse.Namespace())
    assert result == 0


def _set_tenant_args(tenant_id=None, clear=False):
    return argparse.Namespace(tenant_id=tenant_id, clear=clear)


@pytest.mark.asyncio
async def test_auth_set_tenant_stores_the_selection(tmp_path):
    config = _configured_root_config(tmp_path)
    tenant_id = uuid.uuid4()

    result = await auth_set_tenant(config, _set_tenant_args(tenant_id=tenant_id))

    assert result == 0
    assert config.auth.session.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_auth_set_tenant_persists_across_a_reload(tmp_path):
    """Must actually be saved to disk, not just held in memory -- it's read
    back by every later `loom` invocation, which starts a fresh process."""
    config = _configured_root_config(tmp_path)
    tenant_id = uuid.uuid4()
    await auth_set_tenant(config, _set_tenant_args(tenant_id=tenant_id))

    reloaded = UserConfig.load(config_path=config.config_path)
    assert reloaded.auth.session.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_auth_set_tenant_with_no_args_shows_the_current_selection(
    tmp_path, capsys
):
    config = _configured_root_config(tmp_path)
    tenant_id = uuid.uuid4()
    config.auth.session.tenant_id = tenant_id

    result = await auth_set_tenant(config, _set_tenant_args())

    assert result == 0
    assert str(tenant_id) in capsys.readouterr().out


@pytest.mark.asyncio
async def test_auth_set_tenant_with_no_args_and_no_selection_says_so(tmp_path, capsys):
    config = _configured_root_config(tmp_path)

    result = await auth_set_tenant(config, _set_tenant_args())

    assert result == 0
    assert 'No Tenant selected' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_auth_set_tenant_clear_removes_the_selection(tmp_path):
    config = _configured_root_config(tmp_path)
    config.auth.session.tenant_id = uuid.uuid4()

    result = await auth_set_tenant(config, _set_tenant_args(clear=True))

    assert result == 0
    assert config.auth.session.tenant_id is None


@pytest.mark.asyncio
async def test_auth_whoami_shows_the_locally_selected_tenant(tmp_path, capsys):
    config = _configured_root_config(tmp_path)
    config.auth.session.access_token = pydantic.SecretStr(
        _token_with_claims(sub='user-123')
    )
    tenant_id = uuid.uuid4()
    config.auth.session.tenant_id = tenant_id

    result = await auth_whoami(config, argparse.Namespace())

    assert result == 0
    assert str(tenant_id) in capsys.readouterr().out


def _bootstrap_response(**overrides):
    body = {
        'discovery_url': _DISCOVERY_URL,
        'cli_client_id': 'server-cli',
        'api_base_url': 'https://loom.example.com',
    }
    body.update(overrides)
    return httpx.Response(200, json=body, request=httpx.Request('GET', 'http://x'))


@pytest.mark.asyncio
async def test_auth_login_server_caches_what_the_server_advertises(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        'loom.cli.auth.httpx.get', lambda url, **kw: _bootstrap_response()
    )
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient', _fake_catalog_client_recording_posts([])
    )
    monkeypatch.setattr(
        'loom.cli.auth.discover_oidc', lambda discovery_url: _fake_discovery()
    )

    config = UserConfig(config_path=tmp_path / 'config.yaml')
    result = await auth_login(
        config, _base_args(server='https://loom.example.com', discovery_url=None)
    )

    assert result == 0
    reloaded = UserConfig.load(config_path=config.config_path)
    assert reloaded.auth.discovery_url == _DISCOVERY_URL
    assert reloaded.auth.discovery_cli_client_id == 'server-cli'
    assert reloaded.auth.cli_client_id == DEFAULT_CLI_CLIENT_ID
    assert reloaded.auth.effective_cli_client_id() == DEFAULT_CLI_CLIENT_ID
    assert reloaded.catalog.api_base_url == 'https://loom.example.com'


@pytest.mark.asyncio
async def test_auth_login_server_warns_and_keeps_a_differing_local_client_id(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        'loom.cli.auth.httpx.get', lambda url, **kw: _bootstrap_response()
    )
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient', _fake_catalog_client_recording_posts([])
    )
    monkeypatch.setattr(
        'loom.cli.auth.discover_oidc', lambda discovery_url: _fake_discovery()
    )

    config = UserConfig(config_path=tmp_path / 'config.yaml')
    config.auth.cli_client_id = 'my-local-cli'
    result = await auth_login(
        config, _base_args(server='https://loom.example.com', discovery_url=None)
    )

    assert result == 0
    assert 'differs' in capsys.readouterr().out
    assert config.auth.effective_cli_client_id() == 'my-local-cli'


@pytest.mark.asyncio
async def test_auth_login_server_unreachable_fails(monkeypatch, tmp_path):
    def _boom(url, **kw):
        raise httpx.ConnectError('nope')

    monkeypatch.setattr('loom.cli.auth.httpx.get', _boom)
    config = UserConfig(config_path=tmp_path / 'config.yaml')
    result = await auth_login(
        config, _base_args(server='https://loom.example.com', discovery_url=None)
    )
    assert result == 1


def test_effective_cli_client_id_defaults_to_loom_cli(tmp_path):
    config = UserConfig(config_path=tmp_path / 'config.yaml')
    assert config.auth.effective_cli_client_id() == 'loom-cli'
