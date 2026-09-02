import dataclasses
import uuid

from loom.domain.base import AggregateRoot
from loom.domain.errors import ValidationError


@dataclasses.dataclass(kw_only=True)
class DataProduct(AggregateRoot):
    """A curated, contract-bearing, versioned publication over DataSources."""

    contract: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class DataProductLineage:
    """One upstream source (DataSource or DataProduct) feeding a
    DataProduct. Reachable only through the owning DataProduct Aggregate
    -- no repository of its own (CONTEXT.md's "Aggregate (root)" entry)."""

    source_datasource_id: uuid.UUID | None = None
    source_dataproduct_id: uuid.UUID | None = None
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        refs = [self.source_datasource_id, self.source_dataproduct_id]
        if sum(ref is not None for ref in refs) != 1:
            raise ValidationError(
                'A DataProductLineage must reference exactly one of '
                'source_datasource_id/source_dataproduct_id'
            )
