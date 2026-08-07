import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import (
    Base,
    VersionedEntityMixin,
    current_version_index,
    enum_column,
)
from loom.model.enums import DataSourceKind


class DataSource(Base, VersionedEntityMixin):
    """Governed raw data connection (DB, API, vector store, stream)."""

    __tablename__ = 'datasource'
    __table_args__ = (
        sa.UniqueConstraint(
            'entity_id', 'version', name='uq_datasource_entity_version'
        ),
        current_version_index('datasource'),
    )

    kind: Mapped[DataSourceKind] = mapped_column(
        enum_column(DataSourceKind, 'datasource_kind')
    )
    connection_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), default=None
    )
