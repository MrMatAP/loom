import argparse

import pytest
import pytest_asyncio
import sqlalchemy as sa

from loom.cli.db import db_upgrade
from loom.cli.operator import OPERATOR_ROLES, operator_grant, operator_list
from loom.config import AdminConfig
from loom.persistence.engine import get_session_factory
from loom.persistence.governance import AuditEvent
from loom.persistence.tenant import Principal, Tenant
from tests.cli.fake_keycloak import FakeKeycloakAdminClient

_DISCOVERY = 'https://idp.example/realms/loom/.well-known/openid-configuration'


def _args(**overrides):
    defaults = {
        'discovery_url': _DISCOVERY,
        'admin_username': 'admin',
        'admin_password': 'hunter2',
        'admin_client_id': 'admin-cli',
        'username': 'alice',
        'tenant': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def keycloak(monkeypatch):
    captured: dict = {}

    class _Fake(FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, **kwargs):
            if 'client' not in captured:
                captured['client'] = await super().login(issuer, **kwargs)
                captured['client'].known_users.update(
                    {'alice': 'alice-sub', 'bob': 'bob-sub'}
                )
            return captured['client']

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _Fake)
    return captured


@pytest_asyncio.fixture
async def config(tmp_path, monkeypatch) -> AdminConfig:
    """An admin config against a migrated sqlite database (default Tenant
    seeded), with the API client registered."""
    config = AdminConfig(config_path=tmp_path / 'admin.yaml')
    db_path = tmp_path / 'loom.db'
    monkeypatch.setattr(
        type(config.database), 'dsn', property(lambda self: f'sqlite:///{db_path}')
    )
    config.auth.audience = 'loom-catalog-api'
    await db_upgrade(config, argparse.Namespace())
    return config


def _principals(config: AdminConfig) -> list[tuple[str, str]]:
    with get_session_factory(config.database)() as session:
        return [
            (p.external_id, t.slug)
            for p, t in session.execute(
                sa.select(Principal, Tenant).join(
                    Tenant, Tenant.id == Principal.tenant_id
                )
            )
        ]


def test_operator_roles_are_exactly_the_documented_pair():
    assert OPERATOR_ROLES == ['platform-operator', 'catalog-maintainer']


@pytest.mark.asyncio
async def test_grant_assigns_roles_and_creates_a_principal_in_the_default_tenant(
    keycloak, config
):
    assert await operator_grant(config, _args()) == 0

    assert keycloak['client'].granted_roles == [
        ('alice', 'loom-catalog-api', OPERATOR_ROLES)
    ]
    assert _principals(config) == [('alice-sub', 'default')]
    with get_session_factory(config.database)() as session:
        event = session.scalars(sa.select(AuditEvent)).one()
    assert event.action == 'principal.create'
    assert event.details == {'actor_external_id': 'loom-admin'}


@pytest.mark.asyncio
async def test_grant_into_a_named_tenant_by_slug_or_id(keycloak, config):
    with get_session_factory(config.database)() as session:
        other = Tenant(slug='other', name='Other')
        session.add(other)
        session.commit()
        other_id = str(other.id)

    assert await operator_grant(config, _args(tenant='other')) == 0
    assert await operator_grant(config, _args(username='bob', tenant=other_id)) == 0

    assert sorted(_principals(config)) == [
        ('alice-sub', 'other'),
        ('bob-sub', 'other'),
    ]


@pytest.mark.asyncio
async def test_grant_is_idempotent(keycloak, config, capsys):
    await operator_grant(config, _args())
    assert await operator_grant(config, _args()) == 0

    assert _principals(config) == [('alice-sub', 'default')]
    assert 'already has a Principal' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_grant_to_an_unknown_tenant_changes_nothing(keycloak, config):
    assert await operator_grant(config, _args(tenant='nope')) == 1
    assert 'client' not in keycloak
    assert _principals(config) == []


@pytest.mark.asyncio
async def test_grant_fails_for_an_unknown_user(keycloak, config):
    assert await operator_grant(config, _args(username='nobody')) == 1
    assert keycloak['client'].granted_roles == []
    assert _principals(config) == []


@pytest.mark.asyncio
async def test_grant_requires_a_registered_api_client(keycloak, config):
    config.auth.audience = ''
    assert await operator_grant(config, _args()) == 1


@pytest.mark.asyncio
async def test_list_shows_operators_with_their_tenants(keycloak, config, capsys):
    await operator_grant(config, _args())
    capsys.readouterr()

    assert await operator_list(config, _args()) == 0

    out = capsys.readouterr().out
    assert 'alice' in out
    assert 'alice-sub' in out
    assert 'platform-operator,catalog-maintainer' in out
    assert 'default' in out


@pytest.mark.asyncio
async def test_list_with_no_operators(keycloak, config, capsys):
    assert await operator_list(config, _args()) == 0
    assert 'No Platform Operators' in capsys.readouterr().out
