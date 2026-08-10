import argparse
import time
import typing
import uuid

import pydantic
import pytest

from loom.catalog_client import CatalogApiError
from loom.cli.catalog import agent_create, capability_create, model_create
from loom.config import RootConfig


def _logged_in_config(tmp_path) -> RootConfig:
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    config.catalog.api_base_url = 'https://api.example.com'
    config.auth.session.access_token = pydantic.SecretStr('access-tok')
    config.auth.session.expires_at = int(time.time()) + 300
    return config


class _FakeCatalogClient:
    """Records the last `post()` call; `capture` is a shared dict so tests
    can inspect what a CLI command sent without depending on instance
    identity (the CLI functions construct their own client)."""

    last_call: typing.ClassVar[dict | None] = None
    response: typing.ClassVar[dict] = {}
    error: typing.ClassVar[CatalogApiError | None] = None

    def __init__(self, api_base_url, access_token, **kwargs):
        del kwargs
        self.api_base_url = api_base_url
        self.access_token = access_token

    async def post(self, path, payload):
        type(self).last_call = {'path': path, 'payload': payload}
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
async def test_create_reports_401_as_reauth_hint(tmp_path, capsys):
    config = _logged_in_config(tmp_path)
    _FakeCatalogClient.error = CatalogApiError(401, 'Invalid token: expired')

    result = await agent_create(config, _agent_args())

    assert result == 1
    assert 'loom auth login' in capsys.readouterr().out
