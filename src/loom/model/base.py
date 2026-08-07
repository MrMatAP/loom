import datetime
import enum
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from loom.model.enums import Classification, LifecycleState, MaturityLevel


class Base(DeclarativeBase):
    """Declarative base for all Loom registry ORM models."""


class TimestampMixin:
    """Adds server-tracked `created_at`/`updated_at` columns."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


PortableJSON = sa.JSON().with_variant(JSONB(), 'postgresql')


def enum_column(enum_cls: type[enum.Enum], name: str) -> sa.Enum:
    """A SQLAlchemy Enum type that persists the member *value* (matches the wire schema), not its name."""
    return sa.Enum(
        enum_cls, name=name, values_callable=lambda obj: [e.value for e in obj]
    )


def current_version_index(table_name: str) -> sa.Index:
    """Partial unique index enforcing exactly one `is_current` row per `entity_id`."""
    return sa.Index(
        f'ix_{table_name}_current_version',
        'entity_id',
        unique=True,
        postgresql_where=sa.text('is_current'),
        sqlite_where=sa.text('is_current'),
    )


def exactly_one_of(*columns: str) -> str:
    """SQL CHECK expression requiring exactly one of the given columns to be non-null."""
    clauses = []
    for chosen in columns:
        others = [c for c in columns if c != chosen]
        conditions = [f'{chosen} IS NOT NULL'] + [
            f'{other} IS NULL' for other in others
        ]
        clauses.append('(' + ' AND '.join(conditions) + ')')
    return '(' + ' OR '.join(clauses) + ')'


class VersionedEntityMixin:
    """
    Shared columns for every versioned entity (Agent, Skill, Tool, Capability,
    DataSource, DataProduct). Versioning is row-per-version and immutable: a
    content or lifecycle change always inserts a new row. `is_current` is the
    one field that legitimately mutates on the prior row, as bookkeeping when
    a new version is inserted — it is not a content or lifecycle edit.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), primary_key=True, default=uuid.uuid4
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), default=uuid.uuid4, index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer(), default=1)
    is_current: Mapped[bool] = mapped_column(sa.Boolean(), default=True)
    slug: Mapped[str] = mapped_column(sa.String(255), index=True)
    name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text(), default=None)
    lifecycle_state: Mapped[LifecycleState] = mapped_column(
        enum_column(LifecycleState, 'lifecycle_state'),
        default=LifecycleState.DRAFT,
    )
    maturity: Mapped[MaturityLevel] = mapped_column(
        enum_column(MaturityLevel, 'maturity_level'),
        default=MaturityLevel.EXPERIMENTAL,
    )
    classification: Mapped[Classification] = mapped_column(
        enum_column(Classification, 'classification'),
        default=Classification.INTERNAL,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    approved_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), default=None
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tenant.id'), index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id')
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id')
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), default=None
    )
