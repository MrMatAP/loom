import dataclasses
import uuid

from loom.domain.base import AggregateRoot
from loom.domain.enums import DataSourceKind


@dataclasses.dataclass(kw_only=True)
class DataSource(AggregateRoot):
    """Governed raw data connection (DB, API, vector store, stream)."""

    kind: DataSourceKind
    connection_binding_id: uuid.UUID | None = None
