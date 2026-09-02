import decimal
import uuid

import pytest

from loom.domain.capability import CapabilityRealization
from loom.domain.enums import RealizingEntityType
from loom.domain.errors import ValidationError


def test_realization_requires_exactly_one_realizer():
    with pytest.raises(ValidationError):
        CapabilityRealization(
            realizing_entity_type=RealizingEntityType.AGENT,
            contribution_weight=decimal.Decimal('0.5'),
        )


def test_realization_rejects_two_realizers():
    with pytest.raises(ValidationError):
        CapabilityRealization(
            realizing_entity_type=RealizingEntityType.AGENT,
            contribution_weight=decimal.Decimal('0.5'),
            realizing_agent_id=uuid.uuid4(),
            realizing_skill_id=uuid.uuid4(),
        )


def test_realization_type_must_match_the_populated_reference():
    with pytest.raises(ValidationError):
        CapabilityRealization(
            realizing_entity_type=RealizingEntityType.AGENT,
            contribution_weight=decimal.Decimal('0.5'),
            realizing_tool_id=uuid.uuid4(),
        )


def test_realization_with_matching_reference_is_fine():
    realization = CapabilityRealization(
        realizing_entity_type=RealizingEntityType.TOOL,
        contribution_weight=decimal.Decimal('0.25'),
        realizing_tool_id=uuid.uuid4(),
    )
    assert realization.realizing_tool_id is not None
