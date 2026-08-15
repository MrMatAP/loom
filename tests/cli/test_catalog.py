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
    dataproduct_create,
    dataproduct_lineage_add,
    dataproduct_lineage_list,
    dataproduct_update,
    datasource_create,
    datasource_update,
    environment_create,
    environment_list,
    environment_show,
    environment_update,
    model_create,
    model_update,
    principal_create,
    principal_list,
    principal_show,
    resource_list,
    resource_show,
    resource_transition,
    resource_versions,
    skill_create,
    skill_edge_add,
    skill_edge_list,
    skill_node_add,
    skill_node_list,
    skill_update,
    tenant_create,
    tenant_list,
    tenant_show,
    tenant_update,
    tool_binding_add,
    tool_binding_list,
    tool_create,
    tool_update,
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
    last_init_kwargs: typing.ClassVar[dict] = {}
    response: typing.ClassVar[typing.Any] = {}
    error: typing.ClassVar[CatalogApiError | None] = None

    def __init__(self, api_base_url, access_token, **kwargs):
        type(self).last_init_kwargs = kwargs
        self.api_base_url = api_base_url
        self.access_token = access_token
        self._tenant_id = kwargs.get('tenant_id')

    def tenant_path(self, suffix: str) -> str:
        return f'/api/v1/tenants/{self._tenant_id}{suffix}'

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
    _FakeCatalogClient.last_init_kwargs = {}
    _FakeCatalogClient.response = {'entity_id': str(uuid.uuid4()), 'name': 'created'}
    _FakeCatalogClient.error = None
    monkeypatch.setattr('loom.cli.catalog.CatalogClient', _FakeCatalogClient)


TENANT_ID = uuid.uuid4()


def _agent_args(**overrides):
    defaults = {
        'name': 'My Agent',
        'description': None,
        'layer': 'business_tech',
        'model_binding_id': None,
        'llm_config': '{}',
        'prompt': 'You are a helpful assistant.',
        'memory_scope': 'session',
        'permission_boundary': '{}',
        'tenant_id': TENANT_ID,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _capability_args(**overrides):
    defaults = {
        'name': 'My Capability',
        'description': None,
        'target_metrics': '[]',
        'tenant_id': TENANT_ID,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _model_args(**overrides):
    defaults = {
        'name': 'My Model',
        'description': None,
        'protocol': 'anthropic_messages',
        'base_url': None,
        'model': 'claude-opus-4',
        'auth_binding_id': None,
        'tenant_id': TENANT_ID,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _skill_args(**overrides):
    defaults = {
        'name': 'My Skill',
        'description': None,
        'layer': 'business_tech',
        'kind': 'atomic',
        'is_entry_point': False,
        'atomic_content': None,
        'tenant_id': TENANT_ID,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _tool_args(**overrides):
    defaults = {
        'name': 'My Tool',
        'description': None,
        'invocation_spec': '{"method": "GET"}',
        'auth_binding_id': None,
        'tenant_id': TENANT_ID,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _datasource_args(**overrides):
    defaults = {
        'name': 'My DataSource',
        'description': None,
        'kind': 'database',
        'connection_binding_id': None,
        'tenant_id': TENANT_ID,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _dataproduct_args(**overrides):
    defaults = {
        'name': 'My DataProduct',
        'description': None,
        'contract': '{"schema": "v1"}',
        'tenant_id': TENANT_ID,
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

    result = await capability_create(
        config,
        _capability_args(
            description='desc',
            target_metrics='[{"name": "latency"}]',
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/capabilities',
        'payload': {
            'name': 'My Capability',
            'description': 'desc',
            'target_metrics': [{'name': 'latency'}],
        },
    }


@pytest.mark.asyncio
async def test_capability_create_requires_a_tenant(tmp_path, capsys):
    config = _logged_in_config(tmp_path)
    result = await capability_create(config, _capability_args(tenant_id=None))
    assert result == 1
    assert _FakeCatalogClient.last_call is None
    assert 'set-tenant' in capsys.readouterr().out


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
        'path': f'/api/v1/tenants/{TENANT_ID}/model-endpoints',
        'payload': {
            'name': 'My Model',
            'description': None,
            'protocol': 'openai_compatible',
            'base_url': 'https://llm.example.com',
            'model': 'claude-opus-4',
            'auth_binding_id': str(auth_binding_id),
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
        'path': f'/api/v1/tenants/{TENANT_ID}/agents',
        'payload': {
            'name': 'My Agent',
            'description': None,
            'layer': 'business_tech',
            'model_binding_id': str(model_binding_id),
            'llm_config': {'temperature': 0.2},
            'prompt': 'You are a helpful assistant.',
            'memory_scope': 'session',
            'permission_boundary': {'read': True},
        },
    }


@pytest.mark.asyncio
async def test_agent_create_rejects_invalid_llm_config_json(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await agent_create(config, _agent_args(llm_config='not json'))
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_skill_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)

    result = await skill_create(
        config,
        _skill_args(kind='composite', is_entry_point=True, atomic_content=None),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/skills',
        'payload': {
            'name': 'My Skill',
            'description': None,
            'layer': 'business_tech',
            'kind': 'composite',
            'is_entry_point': True,
            'atomic_content': None,
        },
    }


@pytest.mark.asyncio
async def test_skill_create_rejects_invalid_atomic_content_json(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await skill_create(config, _skill_args(atomic_content='not json'))
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_tool_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    auth_binding_id = uuid.uuid4()

    result = await tool_create(config, _tool_args(auth_binding_id=auth_binding_id))

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/tools',
        'payload': {
            'name': 'My Tool',
            'description': None,
            'invocation_spec': {'method': 'GET'},
            'auth_binding_id': str(auth_binding_id),
        },
    }


@pytest.mark.asyncio
async def test_tool_create_rejects_invalid_invocation_spec_json(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await tool_create(config, _tool_args(invocation_spec='not json'))
    assert result == 1
    assert _FakeCatalogClient.last_call is None


@pytest.mark.asyncio
async def test_datasource_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    connection_binding_id = uuid.uuid4()

    result = await datasource_create(
        config, _datasource_args(connection_binding_id=connection_binding_id)
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/datasources',
        'payload': {
            'name': 'My DataSource',
            'description': None,
            'kind': 'database',
            'connection_binding_id': str(connection_binding_id),
        },
    }


@pytest.mark.asyncio
async def test_dataproduct_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)

    result = await dataproduct_create(config, _dataproduct_args())

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/dataproducts',
        'payload': {
            'name': 'My DataProduct',
            'description': None,
            'contract': {'schema': 'v1'},
        },
    }


@pytest.mark.asyncio
async def test_dataproduct_create_rejects_invalid_contract_json(tmp_path):
    config = _logged_in_config(tmp_path)
    result = await dataproduct_create(config, _dataproduct_args(contract='not json'))
    assert result == 1
    assert _FakeCatalogClient.last_call is None


# --- Sub-resource commands: Skill node/edge, Tool binding, DataProduct
# lineage -- keyed on (entity_id, version), so these have their own
# functions rather than going through `resource_*`/`RESOURCES`. -----------


@pytest.mark.asyncio
async def test_skill_node_add_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    result = await skill_node_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            node_key='step-1',
            node_type='agent',
            agent_id=agent_id,
            skill_ref_id=None,
            tool_id=None,
            position='{"x": 1}',
            tenant_id=TENANT_ID,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/skills/{entity_id}/versions/1/nodes',
        'payload': {
            'node_key': 'step-1',
            'node_type': 'agent',
            'agent_id': str(agent_id),
            'skill_ref_id': None,
            'tool_id': None,
            'position': {'x': 1},
        },
    }


@pytest.mark.asyncio
async def test_skill_node_list_gets_expected_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    _FakeCatalogClient.response = []

    result = await skill_node_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=2, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{TENANT_ID}/skills/{entity_id}/versions/2/nodes',
        'params': None,
    }


@pytest.mark.asyncio
async def test_skill_edge_add_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    from_node_id = uuid.uuid4()
    to_node_id = uuid.uuid4()

    result = await skill_edge_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            tenant_id=TENANT_ID,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/skills/{entity_id}/versions/1/edges',
        'payload': {
            'from_node_id': str(from_node_id),
            'to_node_id': str(to_node_id),
        },
    }


@pytest.mark.asyncio
async def test_skill_edge_list_gets_expected_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    _FakeCatalogClient.response = []

    result = await skill_edge_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{TENANT_ID}/skills/{entity_id}/versions/1/edges',
        'params': None,
    }


@pytest.mark.asyncio
async def test_tool_binding_add_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    datasource_id = uuid.uuid4()

    result = await tool_binding_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            datasource_id=datasource_id,
            dataproduct_id=None,
            access_mode='read',
            tenant_id=TENANT_ID,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': (
            f'/api/v1/tenants/{TENANT_ID}/tools/{entity_id}/versions/1/data-bindings'
        ),
        'payload': {
            'datasource_id': str(datasource_id),
            'dataproduct_id': None,
            'access_mode': 'read',
        },
    }


@pytest.mark.asyncio
async def test_tool_binding_list_gets_expected_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    _FakeCatalogClient.response = []

    result = await tool_binding_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': (
            f'/api/v1/tenants/{TENANT_ID}/tools/{entity_id}/versions/1/data-bindings'
        ),
        'params': None,
    }


@pytest.mark.asyncio
async def test_dataproduct_lineage_add_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    source_datasource_id = uuid.uuid4()

    result = await dataproduct_lineage_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            source_datasource_id=source_datasource_id,
            source_dataproduct_id=None,
            tenant_id=TENANT_ID,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': (
            f'/api/v1/tenants/{TENANT_ID}/dataproducts/{entity_id}/versions/1/lineage'
        ),
        'payload': {
            'source_datasource_id': str(source_datasource_id),
            'source_dataproduct_id': None,
        },
    }


@pytest.mark.asyncio
async def test_dataproduct_lineage_list_gets_expected_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    _FakeCatalogClient.response = []

    result = await dataproduct_lineage_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': (
            f'/api/v1/tenants/{TENANT_ID}/dataproducts/{entity_id}/versions/1/lineage'
        ),
        'params': None,
    }


# --- Environment: CRUD (not a VersionedEntity, PATCH-in-place, tenant-nested) -


@pytest.mark.asyncio
async def test_environment_create_posts_expected_payload(tmp_path):
    config = _logged_in_config(tmp_path)

    result = await environment_create(
        config,
        argparse.Namespace(
            name='sandbox-1',
            kind='sandbox',
            compute_boundary_ref='cluster-a',
            network_boundary_ref='vpc-a',
            tenant_id=TENANT_ID,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': f'/api/v1/tenants/{TENANT_ID}/environments',
        'payload': {
            'name': 'sandbox-1',
            'kind': 'sandbox',
            'compute_boundary_ref': 'cluster-a',
            'network_boundary_ref': 'vpc-a',
        },
    }


@pytest.mark.asyncio
async def test_environment_list_gets_expected_path(tmp_path):
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.response = {'items': [], 'total': 0, 'limit': 50, 'offset': 0}

    result = await environment_list(
        config, argparse.Namespace(limit=50, offset=0, tenant_id=TENANT_ID)
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{TENANT_ID}/environments',
        'params': {'limit': 50, 'offset': 0},
    }


@pytest.mark.asyncio
async def test_environment_show_gets_expected_path(tmp_path):
    config = _logged_in_config(tmp_path)
    environment_id = uuid.uuid4()

    result = await environment_show(
        config,
        argparse.Namespace(environment_id=environment_id, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{TENANT_ID}/environments/{environment_id}',
        'params': None,
    }


@pytest.mark.asyncio
async def test_environment_update_drops_unset_fields_from_the_payload(tmp_path):
    """`--compute-boundary-ref` alone must not send an explicit `null` for
    `--network-boundary-ref` -- `_patch_and_show` strips `None` values so
    the PATCH only carries what the caller actually set."""
    config = _logged_in_config(tmp_path)
    environment_id = uuid.uuid4()

    result = await environment_update(
        config,
        argparse.Namespace(
            environment_id=environment_id,
            compute_boundary_ref='cluster-b',
            network_boundary_ref=None,
            tenant_id=TENANT_ID,
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'PATCH',
        'path': f'/api/v1/tenants/{TENANT_ID}/environments/{environment_id}',
        'payload': {'compute_boundary_ref': 'cluster-b'},
    }


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
        'path': f'/api/v1/tenants/{TENANT_ID}/capabilities/{entity_id}/versions',
        'payload': {
            'name': 'Renamed',
            'description': None,
            'target_metrics': [],
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
        f'/api/v1/tenants/{TENANT_ID}/model-endpoints/{entity_id}/versions'
    )


@pytest.mark.asyncio
async def test_agent_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await agent_update(config, _agent_args(entity_id=entity_id))

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{TENANT_ID}/agents/{entity_id}/versions'
    )


@pytest.mark.asyncio
async def test_skill_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await skill_update(config, _skill_args(entity_id=entity_id))

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{TENANT_ID}/skills/{entity_id}/versions'
    )


@pytest.mark.asyncio
async def test_tool_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await tool_update(config, _tool_args(entity_id=entity_id))

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{TENANT_ID}/tools/{entity_id}/versions'
    )


@pytest.mark.asyncio
async def test_datasource_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await datasource_update(config, _datasource_args(entity_id=entity_id))

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{TENANT_ID}/datasources/{entity_id}/versions'
    )


@pytest.mark.asyncio
async def test_dataproduct_update_posts_to_entity_versions_path(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await dataproduct_update(config, _dataproduct_args(entity_id=entity_id))

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{TENANT_ID}/dataproducts/{entity_id}/versions'
    )


# --- Generic verbs: list/show/versions/transition (capability exemplar) ---


@pytest.mark.asyncio
async def test_resource_list_gets_with_filters_and_prints_summary(tmp_path, capsys):
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.response = {
        'items': [
            {
                'entity_id': 'e1',
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
        argparse.Namespace(
            lifecycle_state='draft', limit=10, offset=0, tenant_id=TENANT_ID
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{TENANT_ID}/capabilities',
        'params': {
            'lifecycle_state': 'draft',
            'limit': 10,
            'offset': 0,
        },
    }
    out = capsys.readouterr().out
    assert '1 of 1 shown' in out


@pytest.mark.asyncio
async def test_resource_list_requires_a_tenant(tmp_path, capsys):
    config = _logged_in_config(tmp_path)
    result = await resource_list(
        RESOURCES['capability'],
        config,
        argparse.Namespace(lifecycle_state=None, limit=50, offset=0, tenant_id=None),
    )
    assert result == 1
    assert _FakeCatalogClient.last_call is None
    assert 'set-tenant' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_resource_show_gets_current_version_by_default(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await resource_show(
        RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=None, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{TENANT_ID}/capabilities/{entity_id}',
        'params': None,
    }


@pytest.mark.asyncio
async def test_resource_show_gets_specific_version_when_given(tmp_path):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()

    result = await resource_show(
        RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=2, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{TENANT_ID}/capabilities/{entity_id}/versions/2'
    )


@pytest.mark.asyncio
async def test_resource_versions_gets_all_versions_and_prints_count(tmp_path, capsys):
    config = _logged_in_config(tmp_path)
    entity_id = uuid.uuid4()
    _FakeCatalogClient.response = [
        {
            'entity_id': str(entity_id),
            'name': 'X',
            'version': 1,
            'lifecycle_state': 'draft',
            'maturity': 'experimental',
        },
        {
            'entity_id': str(entity_id),
            'name': 'X v2',
            'version': 2,
            'lifecycle_state': 'in_review',
            'maturity': 'experimental',
        },
    ]

    result = await resource_versions(
        RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, tenant_id=TENANT_ID),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{TENANT_ID}/capabilities/{entity_id}/versions',
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
        argparse.Namespace(
            entity_id=entity_id, version=1, to_state='in_review', tenant_id=TENANT_ID
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'POST',
        'path': (
            f'/api/v1/tenants/{TENANT_ID}/capabilities/{entity_id}'
            '/versions/1/transitions'
        ),
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
        argparse.Namespace(
            lifecycle_state=None, limit=50, offset=0, tenant_id=TENANT_ID
        ),
    )

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{TENANT_ID}{RESOURCES[label].api_path}'
    )


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
    _FakeCatalogClient.error = CatalogApiError(422, 'name already exists')

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
async def test_create_reports_403_with_tenant_guidance(tmp_path, capsys):
    """The common failure under the tenant-path model is 403 (token fine,
    just no Principal in the named Tenant), not 401 -- must not fall
    through to the bare status-code line with no actionable guidance."""
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.error = CatalogApiError(
        403, 'No principal provisioned for this identity in tenant deadbeef'
    )

    result = await agent_create(config, _agent_args())

    out = capsys.readouterr().out
    assert result == 1
    assert 'No principal provisioned for this identity in tenant' in out
    assert '--tenant-id' in out
    assert 'loom auth set-tenant' in out


@pytest.mark.asyncio
async def test_resource_list_requires_login(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await resource_list(
        RESOURCES['capability'],
        config,
        argparse.Namespace(
            lifecycle_state=None, limit=50, offset=0, tenant_id=TENANT_ID
        ),
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
        'path': f'/api/v1/tenants/{tenant_id}/principals',
        'payload': {'kind': 'user', 'external_id': 'sub-123'},
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
        'path': f'/api/v1/tenants/{tenant_id}/principals',
        'params': {'limit': 50, 'offset': 0},
    }


@pytest.mark.asyncio
async def test_principal_list_defaults_to_the_locally_selected_tenant(tmp_path):
    """No --tenant-id given -- falls back to `config.auth.session.tenant_id`
    (normally set automatically by `loom auth login`; see
    `_select_tenant` in `src/loom/cli/auth.py`) rather than erroring."""
    config = _logged_in_config(tmp_path)
    selected_tenant_id = uuid.uuid4()
    config.auth.session.tenant_id = selected_tenant_id
    _FakeCatalogClient.response = {'items': [], 'total': 0, 'limit': 50, 'offset': 0}

    result = await principal_list(
        config, argparse.Namespace(tenant_id=None, limit=50, offset=0)
    )

    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{selected_tenant_id}/principals',
        'params': {'limit': 50, 'offset': 0},
    }


@pytest.mark.asyncio
async def test_principal_list_explicit_tenant_id_beats_the_local_selection(tmp_path):
    """An explicit --tenant-id (e.g. a platform admin listing a Tenant
    other than their own selection) must win -- both in the URL path and
    the client's own tenant_id, not just one of the two."""
    config = _logged_in_config(tmp_path)
    config.auth.session.tenant_id = uuid.uuid4()
    explicit_tenant_id = uuid.uuid4()
    _FakeCatalogClient.response = {'items': [], 'total': 0, 'limit': 50, 'offset': 0}

    result = await principal_list(
        config, argparse.Namespace(tenant_id=explicit_tenant_id, limit=50, offset=0)
    )

    assert result == 0
    assert _FakeCatalogClient.last_call['path'] == (
        f'/api/v1/tenants/{explicit_tenant_id}/principals'
    )
    assert _FakeCatalogClient.last_init_kwargs['tenant_id'] == explicit_tenant_id


@pytest.mark.asyncio
async def test_principal_list_without_tenant_id_or_selection_fails_with_a_message(
    tmp_path, capsys
):
    config = _logged_in_config(tmp_path)
    assert config.auth.session.tenant_id is None

    result = await principal_list(
        config, argparse.Namespace(tenant_id=None, limit=50, offset=0)
    )

    assert result == 1
    assert _FakeCatalogClient.last_call is None
    assert 'set-tenant' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_principal_show_gets_by_id(tmp_path):
    config = _logged_in_config(tmp_path)
    tenant_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    result = await principal_show(
        config, argparse.Namespace(principal_id=principal_id, tenant_id=tenant_id)
    )
    assert result == 0
    assert _FakeCatalogClient.last_call == {
        'method': 'GET',
        'path': f'/api/v1/tenants/{tenant_id}/principals/{principal_id}',
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


@pytest.mark.parametrize(
    'resource',
    ['capability', 'model', 'agent', 'skill', 'tool', 'datasource', 'dataproduct'],
)
@pytest.mark.parametrize('verb', ['list', 'show', 'versions', 'transition'])
def test_generic_verbs_are_registered_for_every_resource(parser, resource, verb):
    sub = parser._subparsers._group_actions[0].choices[resource]
    verb_names = sub._subparsers._group_actions[0].choices.keys()
    assert verb in verb_names


@pytest.mark.parametrize(
    'resource',
    ['capability', 'model', 'agent', 'skill', 'tool', 'datasource', 'dataproduct'],
)
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
            'My Name',
        ]
    )
    assert args.name == 'My Name'
    assert isinstance(args.entity_id, uuid.UUID)


@pytest.mark.parametrize(
    'resource',
    ['capability', 'model', 'agent', 'skill', 'tool', 'datasource', 'dataproduct'],
)
@pytest.mark.parametrize('verb', ['list', 'show', 'versions', 'transition'])
def test_every_generic_verb_takes_an_optional_tenant_id_override(
    parser, resource, verb
):
    sub = parser._subparsers._group_actions[0].choices[resource]
    verb_parser = sub._subparsers._group_actions[0].choices[verb]
    assert any(a.dest == 'tenant_id' for a in verb_parser._actions)


def test_tenant_has_crud_verbs_but_not_versioned_verbs(parser):
    """Tenant isn't a VersionedEntity -- create/list/show/update, but not
    versions/transition."""
    sub = parser._subparsers._group_actions[0].choices['tenant']
    verb_names = set(sub._subparsers._group_actions[0].choices.keys())
    assert {'create', 'list', 'show', 'update'} <= verb_names
    assert not {'versions', 'transition'} & verb_names


def test_environment_has_crud_verbs_but_not_versioned_verbs(parser):
    """Environment isn't a VersionedEntity either -- same shape as Tenant,
    but (unlike Tenant) nested under a Tenant path."""
    sub = parser._subparsers._group_actions[0].choices['environment']
    verb_names = set(sub._subparsers._group_actions[0].choices.keys())
    assert {'create', 'list', 'show', 'update'} <= verb_names
    assert not {'versions', 'transition'} & verb_names


def test_environment_verbs_take_an_optional_tenant_id_override(parser):
    sub = parser._subparsers._group_actions[0].choices['environment']
    for verb in ('create', 'list', 'show', 'update'):
        verb_parser = sub._subparsers._group_actions[0].choices[verb]
        assert any(a.dest == 'tenant_id' for a in verb_parser._actions)


def test_skill_and_tool_and_dataproduct_have_their_sub_resource_verbs(parser):
    """Skill/Tool/DataProduct each add a sub-resource verb group beyond the
    generic six -- confirms `_add_skill_parsers`/`_add_tool_parsers`/
    `_add_dataproduct_parsers` actually registered them."""
    skill_sub = parser._subparsers._group_actions[0].choices['skill']
    assert {'node', 'edge'} <= set(skill_sub._subparsers._group_actions[0].choices)

    tool_sub = parser._subparsers._group_actions[0].choices['tool']
    assert 'binding' in tool_sub._subparsers._group_actions[0].choices

    dataproduct_sub = parser._subparsers._group_actions[0].choices['dataproduct']
    assert 'lineage' in dataproduct_sub._subparsers._group_actions[0].choices


def test_skill_is_entry_point_flag_can_be_explicitly_negated(parser):
    """`--is-entry-point` is a `BooleanOptionalAction`, not `store_true` --
    a Skill `update` restates the entire payload as a new version row (see
    `SkillService.create_new_version`), so a plain `store_true` flag would
    make it impossible to explicitly turn `is_entry_point` back off on
    update (silently defaulting to `False` isn't the same as choosing
    it)."""
    args = parser.parse_args(
        [
            'skill',
            'create',
            'My Skill',
            '--layer',
            'business_ops',
            '--kind',
            'composite',
            '--is-entry-point',
        ]
    )
    assert args.is_entry_point is True

    args = parser.parse_args(
        [
            'skill',
            'create',
            'My Skill',
            '--layer',
            'business_ops',
            '--kind',
            'composite',
            '--no-is-entry-point',
        ]
    )
    assert args.is_entry_point is False


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
    """Principal creation is a durable write -- no session default, unlike
    `list`/`show` -- so argparse itself must require --tenant-id."""
    with pytest.raises(SystemExit):
        parser.parse_args(
            ['principal', 'create', '--kind', 'user', '--external-id', 'sub-123']
        )


def test_principal_list_tenant_id_flag_is_optional_at_parse_time(parser):
    """Unlike `principal create`, `list` defaults `--tenant-id` to the
    locally-selected Tenant at runtime (see `principal_list`) -- argparse
    itself must not require it."""
    args = parser.parse_args(['principal', 'list'])
    assert args.tenant_id is None


def test_principal_show_takes_an_optional_tenant_id_override(parser):
    args = parser.parse_args(['principal', 'show', str(uuid.uuid4())])
    assert args.tenant_id is None
