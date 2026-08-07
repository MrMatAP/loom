import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import (
    Base,
    PortableJSON,
    TimestampMixin,
    VersionedEntityMixin,
    current_version_index,
    exactly_one_of,
)


class DataProduct(Base, VersionedEntityMixin):
    """A curated, contract-bearing, versioned publication over DataSources."""

    __tablename__ = 'dataproduct'
    __table_args__ = (
        sa.UniqueConstraint(
            'entity_id', 'version', name='uq_dataproduct_entity_version'
        ),
        current_version_index('dataproduct'),
    )

    contract: Mapped[dict] = mapped_column(PortableJSON, default=dict)


class DataProductLineage(Base, TimestampMixin):
    """One upstream source (DataSource or DataProduct) feeding a DataProduct."""

    __tablename__ = 'dataproduct_lineage'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of('source_datasource_id', 'source_dataproduct_id'),
            name='ck_dataproduct_lineage_one_source',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    dataproduct_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('dataproduct.id'), index=True
    )
    source_datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('datasource.id'), default=None
    )
    source_dataproduct_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('dataproduct.id'), default=None
    )
