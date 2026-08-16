import argparse

import pydantic
import pytest

from loom.cli.idp import idp_register, idp_unregister
from loom.config import RootConfig
from loom.idp.client import catalog_role_definitions

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


def _unregister_args(**overrides):
    defaults = {
        'issuer_url': 'https://idp.example/realms/loom',
        'admin_username': 'admin',
        'admin_password': 'hunter2',
        'admin_realm': 'master',
        'admin_client_id': 'admin-cli',
        'client_id': None,
        'mcp_client_id': None,
        'swagger_client_id': None,
        'cli_client_id': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.mark.asyncio
async def test_idp_register_requires_issuer(tmp_path):
    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    result = await idp_register(config, _base_args(issuer_url=None))
    assert result == 1
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_idp_register_registers_the_api_client_and_declares_roles(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register(config, _base_args())
    assert result == 0

    output = capsys.readouterr().out
    assert 'loom-catalog-api' in output
    assert 'fake-secret-loom-catalog-api' in output
    assert 'Declared 32 roles' in output


@pytest.mark.asyncio
async def test_idp_register_registers_api_then_mcp_then_public_clients_in_order(
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
    result = await idp_register(config, _base_args())
    assert result == 0

    fake = captured['client']
    assert [c['client_id'] for c in fake.registered_confidential_clients] == [
        'loom-catalog-api',
        'loom-catalog-api-mcp',
    ]
    assert [c['client_id'] for c in fake.registered_public_clients] == [
        'loom-catalog-api-swagger',
        'loom-catalog-api-cli',
    ]

    # Roles declared exactly once, against the API client's internal ref.
    assert len(fake.declared_roles) == 1
    declared_ref, declared = fake.declared_roles[0]
    assert declared_ref == FakeKeycloakAdminClient._internal_ref('loom-catalog-api')
    assert len(declared) == len(catalog_role_definitions())


@pytest.mark.asyncio
async def test_idp_register_defaults_client_names_to_indicative_labels(
    monkeypatch, tmp_path
):
    """Each client's human-readable `name` (shown in the Keycloak admin
    console) defaults to something that says what it's for, not a bare
    echo of its `--*-client-id`."""
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
    assert [c['client_name'] for c in fake.registered_confidential_clients] == [
        'Loom :: REST',
        'Loom :: MCP',
    ]
    assert [c['client_name'] for c in fake.registered_public_clients] == [
        'Loom :: Swagger UI',
        'Loom :: CLI',
    ]


@pytest.mark.asyncio
async def test_idp_register_honors_client_name_overrides(monkeypatch, tmp_path):
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
    await idp_register(
        config,
        _base_args(
            client_name='Custom API',
            mcp_client_name='Custom MCP',
            swagger_client_name='Custom Swagger',
            cli_client_name='Custom CLI',
        ),
    )

    fake = captured['client']
    assert [c['client_name'] for c in fake.registered_confidential_clients] == [
        'Custom API',
        'Custom MCP',
    ]
    assert [c['client_name'] for c in fake.registered_public_clients] == [
        'Custom Swagger',
        'Custom CLI',
    ]


@pytest.mark.asyncio
async def test_idp_register_updates_and_saves_all_auth_config_fields(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    result = await idp_register(config, _base_args())
    assert result == 0

    assert config.auth.issuer == 'https://idp.example/realms/loom'
    assert config.auth.audience == 'loom-catalog-api'
    assert config.auth.discovery_url == (
        'https://idp.example/realms/loom/.well-known/openid-configuration'
    )
    assert config.auth.mcp_audience == 'loom-catalog-api-mcp'
    assert config.auth.swagger_client_id == 'loom-catalog-api-swagger'
    assert config.auth.cli_client_id == 'loom-catalog-api-cli'

    reloaded = RootConfig.load(config_path=config_path)
    assert reloaded.auth.audience == 'loom-catalog-api'
    assert reloaded.auth.mcp_audience == 'loom-catalog-api-mcp'
    assert reloaded.auth.swagger_client_id == 'loom-catalog-api-swagger'
    assert reloaded.auth.cli_client_id == 'loom-catalog-api-cli'


@pytest.mark.asyncio
async def test_idp_register_recomputes_discovery_url_from_issuer(monkeypatch, tmp_path):
    """A stale `discovery_url` left over from a previous IdP must be
    overwritten with one derived from the freshly-registered issuer, not
    just nulled out -- `discovery_url` is now the primary, stored value
    (see `security.discover_and_resolve_issuer`), so leaving it unset would
    make a freshly-registered environment start unconfigured."""
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config_path = tmp_path / 'config.yaml'
    seed = RootConfig(config_path=config_path)
    seed.auth.discovery_url = (
        'https://old-idp.example/realms/old/.well-known/openid-configuration'
    )
    seed.save()

    config = RootConfig.load(config_path=config_path)
    assert config.auth.discovery_url is not None

    await idp_register(config, _base_args())

    expected = 'https://idp.example/realms/loom/.well-known/openid-configuration'
    assert config.auth.discovery_url == expected
    reloaded = RootConfig.load(config_path=config_path)
    assert reloaded.auth.discovery_url == expected


@pytest.mark.asyncio
async def test_idp_register_progress_survives_a_later_step_failing(
    monkeypatch, tmp_path
):
    """Each step saves config as it completes, so a run that fails partway
    through (here: the MCP step) still leaves the API step's config
    written -- consistent with every underlying Keycloak call being
    idempotent-on-409, so the documented recovery is "just re-run it"."""

    class _FailingOnMcpClient(FakeKeycloakAdminClient):
        async def register_client(self, *, client_id, client_name, service_account):
            if client_id.endswith('-mcp'):
                raise RuntimeError('simulated failure registering MCP client')
            return await super().register_client(
                client_id=client_id,
                client_name=client_name,
                service_account=service_account,
            )

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FailingOnMcpClient)

    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    with pytest.raises(RuntimeError):
        await idp_register(config, _base_args())

    reloaded = RootConfig.load(config_path=config_path)
    assert reloaded.auth.audience == 'loom-catalog-api'
    assert reloaded.auth.mcp_audience == ''


@pytest.mark.asyncio
async def test_admin_username_env_var_fallback(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
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
    await idp_register(config, _base_args(admin_username=None))
    assert captured['username'] == 'env-admin'
    assert captured['admin_realm'] == 'master'
    assert captured['admin_client_id'] == 'admin-cli'


@pytest.mark.asyncio
async def test_admin_password_prompted_when_flag_and_env_both_absent(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
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
    await idp_register(config, _base_args(admin_password=None))
    assert captured['password'] == 'prompted-pw'
    assert captured['admin_realm'] == 'master'
    assert captured['admin_client_id'] == 'admin-cli'


@pytest.mark.asyncio
async def test_admin_username_flag_beats_env(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del password, kwargs
            captured['username'] = username
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.setenv('LOOM_IDP_ADMIN_USERNAME', 'env-admin')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(admin_username='flag-admin'))
    assert captured['username'] == 'flag-admin'


@pytest.mark.asyncio
async def test_admin_password_flag_beats_env(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del username, kwargs
            captured['password'] = password
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.setenv('LOOM_IDP_ADMIN_PASSWORD', 'env-pw')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args(admin_password='flag-pw'))
    assert captured['password'] == 'flag-pw'


@pytest.mark.asyncio
async def test_idp_register_wires_admin_realm_and_client_id(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del username, password
            captured['admin_realm'] = kwargs['admin_realm']
            captured['admin_client_id'] = kwargs['admin_client_id']
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    assert captured['admin_realm'] == 'master'
    assert captured['admin_client_id'] == 'admin-cli'


@pytest.mark.asyncio
async def test_idp_register_wires_custom_admin_realm_and_client_id(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del username, password
            captured['admin_realm'] = kwargs['admin_realm']
            captured['admin_client_id'] = kwargs['admin_client_id']
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(
        config,
        _base_args(admin_realm='internal-admins', admin_client_id='custom-admin-cli'),
    )

    assert captured['admin_realm'] == 'internal-admins'
    assert captured['admin_client_id'] == 'custom-admin-cli'


# --- idp unregister -----------------------------------------------------


@pytest.mark.asyncio
async def test_idp_unregister_requires_issuer(tmp_path):
    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    result = await idp_unregister(config, _unregister_args(issuer_url=None))
    assert result == 1
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_idp_unregister_requires_a_client_id(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_unregister(config, _unregister_args())
    assert result == 1


@pytest.mark.asyncio
async def test_idp_unregister_deletes_the_ids_a_prior_register_actually_stored(
    monkeypatch, tmp_path
):
    """Deletion must use whatever `idp_register` actually stored in
    `config.auth`, not re-derive the `{client-id}-mcp`/`-swagger`/`-cli`
    naming convention -- proven here with client ids that don't follow
    that convention at all, via `--*-client-name`-style overrides on the
    register side."""
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(
        config,
        _base_args(
            mcp_client_id='custom-mcp',
            swagger_client_id='custom-swagger',
            cli_client_id='custom-cli',
        ),
    )

    class _CapturingKeycloakAdminClient(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, **kwargs):
            del kwargs
            instance = cls(issuer, 'fake-admin-token')
            captured['client'] = instance
            return instance

    captured: dict[str, FakeKeycloakAdminClient] = {}
    monkeypatch.setattr(
        'loom.cli.idp.KeycloakAdminClient', _CapturingKeycloakAdminClient
    )

    result = await idp_unregister(config, _unregister_args())

    assert result == 0
    # Reverse of registration order.
    assert captured['client'].deleted_clients == [
        'custom-cli',
        'custom-swagger',
        'custom-mcp',
        'loom-catalog-api',
    ]


@pytest.mark.asyncio
async def test_idp_unregister_falls_back_to_the_naming_convention_when_unconfigured(
    monkeypatch, tmp_path
):
    """No prior `register` to read stored ids from -- an explicit
    `--client-id` alone must still resolve the other three via the same
    `{client-id}-mcp`/`-swagger`/`-cli` convention `idp_register` uses."""
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
    result = await idp_unregister(
        config, _unregister_args(client_id='loom-catalog-api')
    )

    assert result == 0
    assert captured['client'].deleted_clients == [
        'loom-catalog-api-cli',
        'loom-catalog-api-swagger',
        'loom-catalog-api-mcp',
        'loom-catalog-api',
    ]


@pytest.mark.asyncio
async def test_idp_unregister_clears_and_persists_auth_config_fields(
    monkeypatch, tmp_path
):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)
    await idp_register(config, _base_args())

    result = await idp_unregister(config, _unregister_args())
    assert result == 0

    assert config.auth.issuer == ''
    assert config.auth.audience == ''
    assert config.auth.discovery_url is None
    assert config.auth.mcp_audience == ''
    assert config.auth.swagger_client_id == ''
    assert config.auth.cli_client_id == ''

    reloaded = RootConfig.load(config_path=config_path)
    assert reloaded.auth.audience == ''
    assert reloaded.auth.mcp_audience == ''
    assert reloaded.auth.swagger_client_id == ''
    assert reloaded.auth.cli_client_id == ''


@pytest.mark.asyncio
async def test_idp_unregister_does_not_touch_the_cached_cli_session(
    monkeypatch, tmp_path
):
    """A cached `loom auth login` session is a separate concern (`loom auth
    logout`) -- unregistering the IDP clients shouldn't silently discard
    it, even though it may no longer be usable."""
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())
    config.auth.session.access_token = pydantic.SecretStr('access-tok')
    config.save()

    result = await idp_unregister(config, _unregister_args())

    assert result == 0
    assert config.auth.session.access_token is not None
    assert config.auth.session.access_token.get_secret_value() == 'access-tok'


@pytest.mark.asyncio
async def test_idp_unregister_is_idempotent_when_run_twice(monkeypatch, tmp_path):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register(config, _base_args())

    first = await idp_unregister(config, _unregister_args(client_id='loom-catalog-api'))
    second = await idp_unregister(
        config, _unregister_args(client_id='loom-catalog-api')
    )

    assert first == 0
    assert second == 0
