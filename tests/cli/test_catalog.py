import argparse
import time
import typing
import uuid

import pydantic
import pytest

from loom.catalog_client import CatalogApiError
from loom.cli import catalog
from loom.cli.catalog import (
    RESOURCES,
    add_catalog_parsers,
    agent_create,
    agent_update,
    capability_create,
    capability_update,
    model_create,
    model_update,
    principal_create,
    principal_list,
    principal_show,
    resource_list,
    resource_show,
    resource_transition,
    resource_versions,
    tenant_create,
    tenant_list,
    tenant_show,
    tenant_update,
)
from loom.config import RootConfig


def _logged_in_config(tmp_path) -> RootConfig:
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.catalog.api_base_url = 'https://api.example.com'
    config.auth.session.access_token = pydantic.SecretStr('access-tok')
    config.auth.session.expires_at = int(time.time()) + 300
    return config


class _FakeCatalogClient:
    """Records the last `get()`/`post()` call; class-level state (not
    instance) so tests can inspect what a CLI command sent without
    depending on instance identity -- the CLI functions construct their
    own client."""

    last_call: typing.ClassVar[dict | None] = None
    response: typing.ClassVar[typing.Any] = {}
    error: typing.ClassVar[CatalogApiError | None] = None

    def __init__(self, api_base_url, access_token, **kwargs):
        del kwargs
        self.api_base_url = api_base_url
        self.access_token = access_token

    async def get(self, path, params=None):
        type(self).last_call = {'method': 'GET', 'path': path, 'params': params}
        if type(self).error is not None:
            raise type(self).error
        return type(self).response

    async def post(self, path, payload):
        type(self).last_call = {'method': 'POST', 'path': path, 'payload': payload}
        if type(self).error is not None:
            raise type(self).error
        return type(self).response

    async def patch(self, path, payload):
        type(self).last_call = {'method': 'PATCH', 'path': path, 'payload': payload}
        if type(self).error is not None:
            raise type(self).error
        return type(self).response


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch):
    _FakeCatalogClient.last_call = None
    _FakeCatalogClient.response = {'entity_id': str(uuid.uuid4()), 'slug': 'created'}
    _FakeCatalogClient.error = None
    monkeypatch.setattr('loom.cli.catalog.CatalogClient', _FakeCatalogClient)


def _agent_args(**overrides):
    defaults = {
        'slug': 'my-agent',
        'name': 'My Agent',
        'description': None,
        'layer': 'business_tech',
        'model_binding_id': None,
        'llm_config': '{}',
        'prompt': 'You are a helpful assistant.',
        'memory_scope': 'session',
        'permission_boundary': '{}',
        'owner_id': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _capability_args(**overrides):
    defaults = {
        'slug': 'my-capability',
        'name': 'My Capability',
        'description': None,
        'target_metrics': '[]',
        'owner_id': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _model_args(**overrides):
    defaults = {
        'slug': 'my-model',
        'name': 'My Model',
        'description': None,
        'protocol': 'anthropic_messages',
        'base_url': None,
        'model': 'claude-opus-4',
        'auth_binding_id': None,
        'owner_id': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# --- Formatting helpers ------------------------------------------------


def test_format_cell_renders_none_as_dash():
    assert catalog._format_cell(None) == '-'


def test_format_cell_renders_dict_as_compact_json():
    assert catalog._format_cell({'a': 1, 'b': None}) == '{"a":1,"b":null}'


def test_format_cell_renders_list_as_compact_json():
    assert catalog._format_cell([{'name': 'x'}]) == '[{"name":"x"}]'


def test_format_cell_renders_plain_values_as_str():
    assert catalog._format_cell(3) == '3'
    assert catalog._format_cell('x') == 'x'
    assert catalog._format_cell(True) == 'True'


def test_print_table_and_print_detail_render_without_crashing(capsys):
    catalog._print_table(('slug', 'name'), [{'slug': 'x', 'name': 'X'}])
    catalog._print_detail({'slug': 'x', 'name': 'X', 'description': None})
    out = capsys.readouterr().out
    assert 'slug' in out
    assert 'X' in out


# --- create (existing coverage, now against a client with get() too) ------


@pytest.mark.asyncio
async def test_capability_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    owner_id = uuid.uuid4()

    result = await capability_create(
        config,
        _capability_args(
            description='desc',
            target_metrics='[{"name": "latency"}]',
            owner_id=owner_id,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': '/api/v1/capabilities',
        'payload': {
            'slug': 'my-capability',
            'name': 'My Capability',
            'description': 'desc',
            'target_metrics': [{'name': 'latency'}],
            'owner_id': str(owner_id),
        },
    }


@pytest.mark.asyncio
async def test_capability_create_rejects_invalid_json(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await capability_create(
        config, _capability_args(target_metrics='{not json')
    )
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_capability_create_rejects_wrong_json_type(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await capability_create(config, _capability_args(target_metrics='{}'))
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_model_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    auth_binding_id = uuid.uuid4()

    result = await model_create(
        config,
        _model_args(
            protocol='openai_compatible',
            base_url='https://llm.example.com',
            auth_binding_id=auth_binding_id,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': '/api/v1/model-endpoints',
        'payload': {
            'slug': 'my-model',
            'name': 'My Model',
            'description': None,
            'protocol': 'openai_compatible',
            'base_url': 'https://llm.example.com',
            'model': 'claude-opus-4',
            'auth_binding_id': str(auth_binding_id),
            'owner_id': None,
        },
    }


@pytest.mark.asyncio
async def test_agent_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    model_binding_id = uuid.uuid4()

    result = await agent_create(
        config,
        _agent_args(
            model_binding_id=model_binding_id,
            llm_config='{"temperature": 0.2}',
            permission_boundary='{"read": true}',
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': '/api/v1/agents',
        'payload': {
            'slug': 'my-agent',
            'name': 'My Agent',
            'description': None,
            'layer': 'business_tech',
            'model_binding_id': str(model_binding_id),
            'llm_config': {'temperature': 0.2},
            'prompt': 'You are a helpful assistant.',
            'memory_scope': 'session',
            'permission_boundary': {'read': True},
            'owner_id': None,
        },
    }


@pytest.mark.asyncio
async def test_agent_create_rejects_invalid_llm_config_json(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await agent_create(config, _agent_args(llm_config='not json'))
    assert result == 1
    assert _FakeCatalogClient.last_call is None


# --- update: create-new-version (capability exemplar + model/agent smoke) --


@pytest.mark.asyncio
async def test_capability_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await capability_update(
        config,
        _capability_args(entity_id=entity_id, name='Renamed'),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/capabilities/{entity_id}/versions',
        'payload': {
            'slug': 'my-capability',
            'name': 'Renamed',
            'description': None,
            'target_metrics': [],
            'owner_id': None,
        },
    }


@pytest.mark.asyncio
async def test_capability_update_rejects_invalid_json(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await capability_update(
        config, _capability_args(entity_id=uuid.uuid4(), target_metrics='not json')
    )
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_model_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await model_update(config, _model_args(entity_id=entity_id))

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/model-endpoints/{entity_id}/versions'
    )


@pytest.mark.asyncio
async def test_agent_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await agent_update(config, _agent_args(entity_id=entity_id))

    assert result == 0
    assert (
        _FakeCatalogClient.last_call['path'] == f'/api/v1/agents/{entity_id}/versions'
    )


# --- Generic verbs: list/show/versions/transition (capability exemplar) ---


@pytest.mark.asyncio
async def test_resource_list_gets_with_filters_and_prints_summary(tmp_path, capsys):
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.response = {
        'items': [
            {
                'entity_id': 'e1',
                'slug': 'x',
                'name': 'X',
                'version': 1,
                'lifecycle_state': 'draft',
                'maturity': 'experimental',
            }
        ],
        'total': 1,
        'limit': 10,
        'offset': 0,
    }

    result = await resource_list(
        RESOURCES['capability'],
        config,
        argparse.Namespace(lifecycle_state='draft', slug='x', limit=10, offset=0),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': '/api/v1/capabilities',
        'params': {
            'lifecycle_state': 'draft',
            'slug': 'x',
            'limit': 10,
            'offset': 0,
        },
    }
    out = capsys.readouterr().out
    assert '1 of 1 shown' in out


@pytest.mark.asyncio
async def test_resource_show_gets_current_version_by_default(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await resource_show(
        RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=None),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/capabilities/{entity_id}',
        'params': None,
    }


@pytest.mark.asyncio
async def test_resource_show_gets_specific_version_when_given(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await resource_show(
        RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=2),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/capabilities/{entity_id}/versions/2'
    )


@pytest.mark.asyncio
async def test_resource_versions_gets_all_versions_and_prints_count(tmp_path, capsys):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    _FakeCatalogClient.response = [
        {
            'entity_id': str(entity_id),
            'slug': 'x',
            'name': 'X',
            'version': 1,
            'lifecycle_state': 'draft',
            'maturity': 'experimental',
        },
        {
            'entity_id': str(entity_id),
            'slug': 'x',
            'name': 'X v2',
            'version': 2,
            'lifecycle_state': 'in_review',
            'maturity': 'experimental',
        },
    ]

    result = await resource_versions(
        RESOURCES['capability'], config, argparse.Namespace(entity_id=entity_id)
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/capabilities/{entity_id}/versions',
        'params': None,
    }
    assert '2 version(s)' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_resource_transition_posts_to_state(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await resource_transition(
        RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=1, to_state='in_review'),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/capabilities/{entity_id}/versions/1/transitions',
        'payload': {'to_state': 'in_review'},
    }


@pytest.mark.parametrize('label', ['model', 'agent'])
@pytest.mark.asyncio
async def test_resource_list_works_for_every_resource(tmp_path, label):
    """Smoke coverage beyond capability -- confirms RESOURCES[label]'s
    api_path is correctly wired for the other two resources too."""
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.response = {'items': [], 'total': 0, 'limit': 50, 'offset': 0}

    result = await resource_list(
        RESOURCES[label],
        config,
        argparse.Namespace(lifecycle_state=None, slug=None, limit=50, offset=0),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == RESOURCES[label].api_path


# --- Cross-cutting: auth/error handling (verb-agnostic) --------------------


@pytest.mark.asyncio
async def test_create_requires_login(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await agent_create(config, _agent_args())
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_create_requires_unexpired_session(tmp_path):
    config = _logged_in_config(tmp_path)
    config.auth.session.expires_at = int(time.time()) - 10
    result = await agent_create(config, _agent_args())
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_create_reports_api_error(tmp_path):
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.error = CatalogApiError(422, 'slug already exists')

    result = await agent_create(config, _agent_args())

    assert result == 1


@pytest.mark.asyncio
async def test_create_reports_401_with_the_servers_detail(tmp_path, capsys):
    """The 401 message must surface the server's actual reason -- it's the
    only thing that distinguishes "your token expired" from "your account
    isn't provisioned", and re-login only fixes the former."""
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.error = CatalogApiError(
        401, 'No principal provisioned for this identity'
    )

    result = await agent_create(config, _agent_args())

    out = capsys.readouterr().out
    assert result == 1
    assert 'No principal provisioned for this identity' in out
    assert 'loom auth login' in out


@pytest.mark.asyncio
async def test_resource_list_requires_login(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await resource_list(
        RESOURCES['capability'],
        config,
        argparse.Namespace(lifecycle_state=None, slug=None, limit=50, offset=0),
    )
    assert result == 1
    assert _FakeCatalogClient.last_call is None


# --- Tenant/Principal: CRUD ------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await tenant_create(config, argparse.Namespace(slug='acme', name='Acme'))
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': '/api/v1/tenants',
        'payload': {'slug': 'acme', 'name': 'Acme'},
    }


@pytest.mark.asyncio
async def test_tenant_list_gets_with_pagination(tmp_path):
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.response = {'items': [], 'total': 0, 'limit': 50, 'offset': 0}
    result = await tenant_list(config, argparse.Namespace(limit=50, offset=0))
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': '/api/v1/tenants',
        'params': {'limit': 50, 'offset': 0},
    }


@pytest.mark.asyncio
async def test_tenant_show_gets_by_id(tmp_path):
    config = _logged_in_config(tmp_path)
    tenant_id = uuid.uuid4()
    result = await tenant_show(config, argparse.Namespace(tenant_id=tenant_id))
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{tenant_id}',
        'params': None,
    }


@pytest.mark.asyncio
async def test_tenant_update_patches_name_in_place(tmp_path):
    """Unlike Capability/ModelEndpoint/Agent's `update`, Tenant isn't
    versioned -- this must PATCH the same id, not POST a new version."""
    config = _logged_in_config(tmp_path)
    tenant_id = uuid.uuid4()
    result = await tenant_update(
        config, argparse.Namespace(tenant_id=tenant_id, name='Renamed')
    )
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'PATCH',
        'path': f'/api/v1/tenants/{tenant_id}',
        'payload': {'name': 'Renamed'},
    }


@pytest.mark.asyncio
async def test_principal_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    tenant_id = uuid.uuid4()
    result = await principal_create(
        config,
        argparse.Namespace(
            tenant_id=tenant_id,
            kind='user',
            external_id='sub-123',
        ),
    )
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': '/api/v1/principals',
        'payload': {
            'tenant_id': str(tenant_id),
            'kind': 'user',
            'external_id': 'sub-123',
        },
    }


@pytest.mark.asyncio
async def test_principal_list_requires_tenant_id_query_param(tmp_path):
    config = _logged_in_config(tmp_path)
    tenant_id = uuid.uuid4()
    _FakeCatalogClient.response = {'items': [], 'total': 0, 'limit': 50, 'offset': 0}
    result = await principal_list(
        config, argparse.Namespace(tenant_id=tenant_id, limit=50, offset=0)
    )
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': '/api/v1/principals',
        'params': {'tenant_id': str(tenant_id), 'limit': 50, 'offset': 0},
    }


@pytest.mark.asyncio
async def test_principal_show_gets_by_id(tmp_path):
    config = _logged_in_config(tmp_path)
    principal_id = uuid.uuid4()
    result = await principal_show(config, argparse.Namespace(principal_id=principal_id))
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/principals/{principal_id}',
        'params': None,
    }


@pytest.mark.asyncio
async def test_tenant_create_requires_login(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await tenant_create(config, argparse.Namespace(slug='acme', name='Acme'))
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_principal_create_requires_login(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await principal_create(
        config,
        argparse.Namespace(
            tenant_id=uuid.uuid4(),
            kind='user',
            external_id='y',
        ),
    )
    assert result == 1
    assert _FakeCatalogClient.last_call is None


# --- argparse wiring ------------------------------------------------------


@pytest.fixture
def parser():
    import argparse as argparse_module

    top = argparse_module.ArgumentParser('loom')
    subparsers = top.add_subparsers(required=True)
    add_catalog_parsers(subparsers)
    return top


@pytest.mark.parametrize('resource', ['capability', 'model', 'agent'])
@pytest.mark.parametrize('verb', ['list', 'show', 'versions', 'transition'])
def test_generic_verbs_are_registered_for_every_resource(parser, resource, verb):
    sub = parser._subparsers._group_actions[0].choices[resource]
    verb_names = sub._subparsers._group_actions[0].choices.keys()
    assert verb in verb_names


@pytest.mark.parametrize('resource', ['capability', 'model', 'agent'])
def test_create_and_update_are_registered_for_every_resource(parser, resource):
    sub = parser._subparsers._group_actions[0].choices[resource]
    verb_names = sub._subparsers._group_actions[0].choices.keys()
    assert {'create', 'update'} <= set(verb_names)


def test_update_requires_an_entity_id_positional(parser):
    args = parser.parse_args(
        [
            'capability',
            'update',
            str(uuid.uuid4()),
            'my-slug',
            'My Name',
        ]
    )
    assert args.slug == 'my-slug'
    assert isinstance(args.entity_id, uuid.UUID)


def test_tenant_has_crud_verbs_but_not_versioned_verbs(parser):
    """Tenant isn't a VersionedEntity -- create/list/show/update, but not
    versions/transition."""
    sub = parser._subparsers._group_actions[0].choices['tenant']
    verb_names = set(sub._subparsers._group_actions[0].choices.keys())
    assert {'create', 'list', 'show', 'update'} <= verb_names
    assert not {'versions', 'transition'} & verb_names


def test_principal_has_crud_verbs_but_not_update_or_versioned_verbs(parser):
    """Principal isn't a VersionedEntity either, and unlike Tenant has no
    `update` at all -- its only mutable field (display_name) no longer
    exists (see `src/loom/model/tenant.py`'s `Principal` docstring)."""
    sub = parser._subparsers._group_actions[0].choices['principal']
    verb_names = set(sub._subparsers._group_actions[0].choices.keys())
    assert {'create', 'list', 'show'} <= verb_names
    assert not {'versions', 'transition', 'update'} & verb_names


def test_tenant_update_takes_tenant_id_and_name_positionals(parser):
    tenant_id = uuid.uuid4()
    args = parser.parse_args(['tenant', 'update', str(tenant_id), 'Renamed'])
    assert args.tenant_id == tenant_id
    assert args.name == 'Renamed'


def test_principal_create_requires_tenant_id_kind_external_id(parser):
    tenant_id = uuid.uuid4()
    args = parser.parse_args(
        [
            'principal',
            'create',
            '--tenant-id',
            str(tenant_id),
            '--kind',
            'user',
            '--external-id',
            'sub-123',
        ]
    )
    assert args.tenant_id == tenant_id
    assert args.kind == 'user'
    assert args.external_id == 'sub-123'


def test_principal_create_requires_tenant_id_flag_at_parse_time(parser):
    """No session claim to default from any more (a caller's Tenant is
    resolved from their own Principal row, not a token claim), so
    argparse itself must require --tenant-id."""
    with pytest.raises(SystemExit):
        parser.parse_args(
            ['principal', 'create', '--kind', 'user', '--external-id', 'sub-123']
        )


def test_principal_list_requires_tenant_id_flag_at_parse_time(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(['principal', 'list'])
