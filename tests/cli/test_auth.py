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
from loom.config import RootConfig
from loom.idp.device_flow import DeviceAuthorization, DeviceCodeError, DeviceTokens


def _token_with_claims(**claims: typing.Any) -> str:
    return jwt.encode(claims, 'test-secret', algorithm='HS256')


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

    reloaded = RootConfig.load(config_path=config.config_path)
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
async def test_auth_login_survives_a_tenant_listing_api_error(monkeypatch, tmp_path):
    """A login already holding valid tokens must not be thrown away just
    because the best-effort tenant-listing step couldn't reach the API."""
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(error=CatalogApiError(503, 'Service Unavailable')),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert config.auth.session.access_token is not None
    assert config.auth.session.tenant_id is None


@pytest.mark.asyncio
async def test_auth_login_survives_a_tenant_listing_connection_error(
    monkeypatch, tmp_path
):
    """Same as the API-error case above, but for a transport-level failure
    (unreachable `catalog.api_base_url`, DNS, etc.) rather than an HTTP
    error response -- `CatalogClient` doesn't wrap these as
    `CatalogApiError`, so `_select_tenant` must catch them too or a login
    that already holds valid tokens would crash instead of just warning."""
    monkeypatch.setattr('loom.cli.auth.DeviceCodeClient', _FakeDeviceCodeClient)
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client(error=httpx.ConnectError('Connection refused')),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert config.auth.session.access_token is not None
    assert config.auth.session.tenant_id is None


def _device_code_client_with_token(token: str):
    """Like `_FakeDeviceCodeClient`, but `poll()` returns a given access
    token verbatim instead of the plain opaque `'access-tok'` string --
    needed wherever a test must exercise claims read off the token itself
    (`_register_platform_admin_in_default_tenant` reads `scope`/`sub`)."""

    class _Client(_FakeDeviceCodeClient):
        async def poll(self, authorization):
            del authorization
            return DeviceTokens(
                access_token=token, refresh_token='refresh-tok', expires_in=300
            )

    return _Client


def _fake_catalog_client_with_principal_post(
    *, items=(), post_error: Exception | None = None, post_calls: list | None = None
):
    """Extends `_fake_catalog_client` with `POST` support, for tests that
    exercise `_register_platform_admin_in_default_tenant`'s self-
    registration call. Records every `post()` call into `post_calls` (if
    given) so a test can assert on the path/payload without a real API."""

    class _Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def get(self, path, params=None):
            del path, params
            return {'items': list(items), 'total': len(items), 'limit': 50, 'offset': 0}

        async def post(self, path, payload):
            if post_calls is not None:
                post_calls.append((path, payload))
            if post_error is not None:
                raise post_error
            return {'id': str(uuid.uuid4()), **payload}

    return _Client


@pytest.mark.asyncio
async def test_auth_login_registers_platform_admin_in_default_tenant(
    monkeypatch, tmp_path, capsys
):
    """A fresh platform-admin login (full catalog-wide scope, no Principal
    row anywhere yet) auto-provisions itself into the `default` Tenant --
    see `_register_platform_admin_in_default_tenant`."""
    token = _token_with_claims(sub='admin-user', scope='catalog:principal:write')
    monkeypatch.setattr(
        'loom.cli.auth.DeviceCodeClient', _device_code_client_with_token(token)
    )
    tenant_id = uuid.uuid4()
    post_calls: list = []
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client_with_principal_post(
            items=[{'id': str(tenant_id), 'slug': 'default', 'name': 'Default'}],
            post_calls=post_calls,
        ),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert post_calls == [
        (
            f'/api/v1/tenants/{tenant_id}/principals',
            {'kind': 'user', 'external_id': 'admin-user'},
        )
    ]
    assert (
        'Registered a Principal for you in the default Tenant'
        in capsys.readouterr().out
    )


@pytest.mark.asyncio
async def test_auth_login_does_not_register_a_non_admin_identity(monkeypatch, tmp_path):
    """A caller without `catalog:principal:write` (an ordinary Tenant user,
    not a platform admin) must never trigger the self-registration POST --
    it isn't theirs to make, and the server would 403 it anyway."""
    token = _token_with_claims(sub='ordinary-user', scope='catalog:capability:read')
    monkeypatch.setattr(
        'loom.cli.auth.DeviceCodeClient', _device_code_client_with_token(token)
    )
    tenant_id = uuid.uuid4()
    post_calls: list = []
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client_with_principal_post(
            items=[{'id': str(tenant_id), 'slug': 'default', 'name': 'Default'}],
            post_calls=post_calls,
        ),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert post_calls == []


@pytest.mark.asyncio
async def test_auth_login_skips_registration_without_a_default_tenant(
    monkeypatch, tmp_path
):
    """A platform admin still self-registers nowhere if there's no `slug ==
    'default'` Tenant to target -- this step only ever targets that one
    Tenant, never guesses among others."""
    token = _token_with_claims(sub='admin-user', scope='catalog:principal:write')
    monkeypatch.setattr(
        'loom.cli.auth.DeviceCodeClient', _device_code_client_with_token(token)
    )
    post_calls: list = []
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client_with_principal_post(
            items=[{'id': str(uuid.uuid4()), 'slug': 'acme', 'name': 'Acme'}],
            post_calls=post_calls,
        ),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    assert result == 0
    assert post_calls == []


@pytest.mark.asyncio
async def test_auth_login_treats_a_duplicate_principal_as_already_registered(
    monkeypatch, tmp_path, capsys
):
    """A second login for the same platform admin hits the Tenant's
    uniqueness constraint on `(tenant_id, external_id)` -- surfaced as a
    422 -- and that must be silently treated as already-registered, not
    reported as a failure. Uses sqlite's own wording here; production runs
    Postgres, whose message is `duplicate key value violates unique
    constraint "uq_principal_tenant_external_id"` instead -- the
    `'duplicate key' in detail` arm in `_register_platform_admin_in_
    default_tenant` is what covers that phrasing, not this test, so don't
    "simplify" that check down to only the sqlite wording asserted here."""
    token = _token_with_claims(sub='admin-user', scope='catalog:principal:write')
    monkeypatch.setattr(
        'loom.cli.auth.DeviceCodeClient', _device_code_client_with_token(token)
    )
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client_with_principal_post(
            items=[{'id': str(tenant_id), 'slug': 'default', 'name': 'Default'}],
            post_error=CatalogApiError(
                422,
                'UNIQUE constraint failed: principal.tenant_id, principal.external_id',
            ),
        ),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    out = capsys.readouterr().out
    assert result == 0
    assert 'Could not auto-register' not in out


@pytest.mark.asyncio
async def test_auth_login_warns_when_registration_fails_for_another_reason(
    monkeypatch, tmp_path, capsys
):
    """Any other failure (e.g. this scope guess turning out to be wrong
    server-side, or a network error) is a soft warning that still names
    the manual fallback -- never a reason to fail the login."""
    token = _token_with_claims(sub='admin-user', scope='catalog:principal:write')
    monkeypatch.setattr(
        'loom.cli.auth.DeviceCodeClient', _device_code_client_with_token(token)
    )
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(
        'loom.cli.auth.CatalogClient',
        _fake_catalog_client_with_principal_post(
            items=[{'id': str(tenant_id), 'slug': 'default', 'name': 'Default'}],
            post_error=CatalogApiError(403, 'Forbidden'),
        ),
    )

    config = _configured_root_config(tmp_path)
    result = await auth_login(config, _base_args())

    out = capsys.readouterr().out
    assert result == 0
    assert 'Could not auto-register a Principal' in out
    assert 'loom principal create' in out


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

    reloaded = RootConfig.load(config_path=config.config_path)
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
