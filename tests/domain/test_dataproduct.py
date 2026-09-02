import uuid

import pytest

from loom.domain.dataproduct import DataProductLineage
from loom.domain.errors import ValidationError


def test_lineage_requires_exactly_one_source():
    with pytest.raises(ValidationError):
        DataProductLineage()


def test_lineage_rejects_two_sources():
    with pytest.raises(ValidationError):
        DataProductLineage(
            source_datasource_id=uuid.uuid4(), source_dataproduct_id=uuid.uuid4()
        )


def test_lineage_with_one_source_is_fine():
    lineage = DataProductLineage(source_datasource_id=uuid.uuid4())
    assert lineage.source_datasource_id is not None
