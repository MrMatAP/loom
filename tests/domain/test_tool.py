import uuid

import pytest

from loom.domain.enums import DataBindingAccessMode
from loom.domain.errors import ValidationError
from loom.domain.tool import ToolDataBinding


def test_data_binding_requires_exactly_one_target():
    with pytest.raises(ValidationError):
        ToolDataBinding(access_mode=DataBindingAccessMode.READ)


def test_data_binding_rejects_two_targets():
    with pytest.raises(ValidationError):
        ToolDataBinding(
            access_mode=DataBindingAccessMode.READ,
            datasource_id=uuid.uuid4(),
            dataproduct_id=uuid.uuid4(),
        )


def test_data_binding_with_one_target_is_fine():
    binding = ToolDataBinding(
        access_mode=DataBindingAccessMode.READ_WRITE, datasource_id=uuid.uuid4()
    )
    assert binding.datasource_id is not None
