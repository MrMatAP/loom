import dataclasses
import uuid

from loom.domain.base import AggregateRoot
from loom.domain.enums import DataBindingAccessMode
from loom.domain.errors import ValidationError


@dataclasses.dataclass(kw_only=True)
class Tool(AggregateRoot):
    """Static, design-time-bound interface for deterministic external automation."""

    invocation_spec: dict = dataclasses.field(default_factory=dict)
    auth_binding_id: uuid.UUID | None = None


@dataclasses.dataclass
class ToolDataBinding:
    """Static, version-pinned binding of a Tool to a DataSource or
    DataProduct. Reachable only through the owning Tool Aggregate -- no
    repository of its own (CONTEXT.md's "Aggregate (root)" entry)."""

    access_mode: DataBindingAccessMode
    datasource_id: uuid.UUID | None = None
    dataproduct_id: uuid.UUID | None = None
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        refs = [self.datasource_id, self.dataproduct_id]
        if sum(ref is not None for ref in refs) != 1:
            raise ValidationError(
                'A ToolDataBinding must reference exactly one of '
                'datasource_id/dataproduct_id'
            )
