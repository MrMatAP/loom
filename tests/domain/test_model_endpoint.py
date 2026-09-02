import uuid

import pytest

from loom.domain.enums import ModelProtocol
from loom.domain.errors import ValidationError
from loom.domain.model_endpoint import ModelEndpoint


def _endpoint(**overrides) -> ModelEndpoint:
    defaults = {
        'tenant_id': uuid.uuid4(),
        'owner_id': uuid.uuid4(),
        'created_by_id': uuid.uuid4(),
        'name': 'Local LLM',
        'protocol': ModelProtocol.OPENAI_COMPATIBLE,
        'base_url': 'https://llm.internal.example/v1',
        'model': 'llama-4',
    }
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


def test_openai_compatible_requires_base_url():
    with pytest.raises(ValidationError):
        _endpoint(base_url=None)


def test_openai_compatible_with_base_url_is_fine():
    endpoint = _endpoint()
    assert endpoint.base_url is not None


def test_anthropic_messages_does_not_require_base_url():
    endpoint = _endpoint(protocol=ModelProtocol.ANTHROPIC_MESSAGES, base_url=None)
    assert endpoint.base_url is None
