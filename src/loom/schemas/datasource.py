import uuid

from loom.domain.enums import DataSourceKind
from loom.schemas.base import VersionedEntityCreate, VersionedEntityRead


class DataSourceCreate(VersionedEntityCreate):
    kind: DataSourceKind
    connection_binding_id: uuid.UUID | None = None


class DataSourceRead(VersionedEntityRead):
    kind: DataSourceKind
    connection_binding_id: uuid.UUID | None
