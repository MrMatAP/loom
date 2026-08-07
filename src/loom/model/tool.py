import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import (
    Base,
    PortableJSON,
    TimestampMixin,
    VersionedEntityMixin,
    current_version_index,
    enum_column,
    exactly_one_of,
)
from loom.model.enums import DataBindingAccessMode


class Tool(Base, VersionedEntityMixin):
    """Static, design-time-bound interface for deterministic external automation."""

    __tablename__ = 'tool'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_tool_entity_version'),
        current_version_index('tool'),
    )

    invocation_spec: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    auth_binding_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)


class ToolDataBinding(Base, TimestampMixin):
    """Static, version-pinned binding of a Tool to a DataSource or DataProduct."""

    __tablename__ = 'tool_data_binding'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of('datasource_id', 'dataproduct_id'),
            name='ck_tool_data_binding_one_target',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), index=True
    )
    datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('datasource.id'), default=None
    )
    dataproduct_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('dataproduct.id'), default=None
    )
    access_mode: Mapped[DataBindingAccessMode] = mapped_column(
        enum_column(DataBindingAccessMode, 'data_binding_access_mode')
    )
