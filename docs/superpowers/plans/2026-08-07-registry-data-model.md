# Registry Physical Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the Registry (Catalog + Governance + Data-plane) domain model from `CLAUDE.md` as SQLAlchemy 2.0 ORM classes + Pydantic v2 wire schemas under `src/loom/model/`, deployed via an Alembic migration that ships inside the package and is driven entirely through a new `loom db` CLI subcommand — no end-user Alembic usage required.

**Architecture:** Every versioned entity (`Agent`, `Skill`, `Tool`, `Capability`, `DataSource`, `DataProduct`) is row-per-version and immutable via a shared `VersionedEntityMixin`; a partial unique index enforces exactly one `is_current` row per `entity_id`. Polymorphic references (e.g. "this row realizes either an Agent, a Skill, or a Tool") are modeled as N mutually-exclusive nullable foreign keys plus a `CHECK` constraint, not a generic/loose FK, so referential integrity is real. All types are dialect-portable (`sa.Uuid()`, `JSON().with_variant(JSONB(), 'postgresql')`) so the full test suite runs against in-memory SQLite while production runs Postgres unchanged.

**Tech Stack:** SQLAlchemy 2.0 (declarative `Mapped`/`mapped_column`), Pydantic v2, Alembic, `psycopg` v3, pytest, PostgreSQL (target), SQLite (test-only).

## Global Constraints

- Formatter: `ruff format` — single quotes, 88-char lines, 4-space indent, Python 3.14 target (from user's global Python style guide).
- Import order: stdlib, then third-party, then local (`loom.*`), blank line between groups, in every file.
- Type hints on all function signatures; `pathlib.Path` over raw strings for paths.
- No comments on obvious code; short single-line docstrings on public classes only.
- Lint via `ruff check src/`, type-check via `pyrefly check` (not mypy).
- Every versioned entity carries: `lifecycle_state`, `maturity`, `owner`, `classification`, `created_by/at`, `approved_by/at` (spec, shared-entity-attributes rule) — implemented as `VersionedEntityMixin`.
- `Tool.data_bindings[]` is **version-pinned**: `ToolDataBinding` FKs point at a specific `DataSource`/`DataProduct` version row, never at the floating `entity_id`.
- `Agent` never gets a direct data binding (spec principle 2) — `Agent` has no FK to `DataSource`/`DataProduct` anywhere in this schema.
- Raw `Trace`/`TraceStep`/time-series `Metric` points are **out of scope**; only the derived rollup `Metric` row (`realization_score`) is modeled.
- Deviation from the design doc, noted for the record: the doc says IDs are "server-defaulted via Postgres' `gen_random_uuid()`"; this plan instead uses a Python-side `default=uuid.uuid4` on every `Uuid()` primary key. This is functionally equivalent for all ORM-mediated writes, is dialect-portable (works identically on SQLite in tests and Postgres in production), and avoids a DB-function dependency. Flag to the user if server-side defaults are later found necessary (e.g. non-ORM bulk loads).
- Deviation: `Agent.model_config` (from the design doc's field list) is renamed to `Agent.llm_config` throughout (ORM column, Pydantic field) because `model_config` is a reserved attribute name on every Pydantic v2 `BaseModel` (used for `ConfigDict`) — the literal name cannot be used as a field.
- Deviation: `EvalSuite`/`EvalRun` module is named `evaluation.py`, not `eval.py`, to avoid shadowing the Python builtin `eval` as an importable module name.
- No SQLAlchemy `relationship()` attributes are defined anywhere — this is a physical data model exercise; FKs alone provide referential integrity, and the (out-of-scope) REST/service layer defines its own query/aggregation patterns.
- Every "exactly one of N nullable FKs must be set" polymorphic reference uses the shared `exactly_one_of(*columns)` SQL-text helper (Task 2) for a single, tested, portable implementation instead of ad hoc boolean algebra per table.

---

## Task 1: Foundations — deps, enums, Base/TimestampMixin, Tenant, Principal

**Files:**
- Modify: `pyproject.toml`
- Create: `src/loom/model/__init__.py`
- Create: `src/loom/model/enums.py`
- Create: `src/loom/model/base.py`
- Create: `src/loom/model/tenant.py`
- Create: `src/loom/model/schemas/__init__.py`
- Create: `src/loom/model/schemas/tenant.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/model/test_tenant.py`

**Interfaces:**
- Produces: `loom.model.enums.PrincipalKind` (`USER`, `AGENT`, `SERVICE_ACCOUNT`); `loom.model.base.Base` (`DeclarativeBase` subclass), `loom.model.base.TimestampMixin` (`created_at`, `updated_at` columns); `loom.model.tenant.Tenant(id, slug, name)`, `loom.model.tenant.Principal(id, tenant_id, kind, display_name, external_id)`; `loom.model.schemas.tenant.TenantCreate/TenantRead/TenantUpdate`, `PrincipalCreate/PrincipalRead/PrincipalUpdate`; pytest fixtures `engine` and `session` (in-memory SQLite, tables created from `Base.metadata`, foreign keys enforced).

- [ ] **Step 1: Add dependencies to `pyproject.toml`**

Add to the `dependencies` list in `pyproject.toml` (alongside the existing `pydantic`/`pyyaml`/`rich` entries):

```toml
    "sqlalchemy>=2.0.36",
    "alembic>=1.14.0",
    "psycopg[binary]>=3.2.3",
```

Add `"pytest-asyncio>=0.24.0"` to `[dependency-groups].dev` only if not already present — check first with `grep asyncio pyproject.toml` (the existing `pyproject.toml` already sets `asyncio_default_fixture_loop_scope` under `[tool.pytest.ini_options]`, implying `pytest-asyncio` is expected; add the dep if the grep finds no matching package entry).

Run: `uv sync`
Expected: dependencies resolve and install into `.venv` without error.

- [ ] **Step 2: Write the failing test**

```python
# tests/conftest.py
import sqlite3

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from loom.model.base import Base


@sa.event.listens_for(sa.engine.Engine, 'connect')
def _enable_sqlite_fk(dbapi_connection, connection_record) -> None:
    del connection_record
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()


@pytest.fixture
def engine() -> sa.Engine:
    test_engine = sa.create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    yield test_engine
    Base.metadata.drop_all(test_engine)


@pytest.fixture
def session(engine: sa.Engine):
    with Session(engine) as test_session:
        yield test_session
```

```python
# tests/model/test_tenant.py
import uuid

import sqlalchemy as sa
import pytest

from loom.model.enums import PrincipalKind
from loom.model.tenant import Principal, Tenant
from loom.model.schemas.tenant import PrincipalCreate, PrincipalRead, TenantCreate, TenantRead


def test_tenant_round_trip(session):
    tenant = Tenant(**TenantCreate(slug='acme', name='Acme Corp').model_dump())
    session.add(tenant)
    session.commit()

    read = TenantRead.model_validate(tenant)
    assert read.slug == 'acme'
    assert isinstance(read.id, uuid.UUID)


def test_principal_unique_external_id_per_tenant(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()

    principal = Principal(
        **PrincipalCreate(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            display_name='Ada',
            external_id='ada@acme.example',
        ).model_dump()
    )
    session.add(principal)
    session.commit()

    read = PrincipalRead.model_validate(principal)
    assert read.kind == PrincipalKind.USER

    session.add(
        Principal(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            display_name='Ada 2',
            external_id='ada@acme.example',
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()
```

Create empty `tests/__init__.py` and `tests/model/__init__.py`.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/model/test_tenant.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model'`

- [ ] **Step 4: Implement**

```python
# src/loom/model/__init__.py
"""SQLAlchemy ORM and Pydantic wire schemas for the Loom registry."""
```

```python
# src/loom/model/enums.py
import enum


class LifecycleState(enum.StrEnum):
    DRAFT = 'draft'
    IN_REVIEW = 'in_review'
    APPROVED = 'approved'
    PUBLISHED = 'published'
    DEPRECATED = 'deprecated'
    RETIRED = 'retired'


class MaturityLevel(enum.StrEnum):
    EXPERIMENTAL = 'experimental'
    BETA = 'beta'
    STABLE = 'stable'
    DEPRECATED = 'deprecated'


class Classification(enum.StrEnum):
    PUBLIC = 'public'
    INTERNAL = 'internal'
    CONFIDENTIAL = 'confidential'
    RESTRICTED = 'restricted'


class Layer(enum.StrEnum):
    INFRA_OPS = 'infra_ops'
    BUSINESS_TECH = 'business_tech'
    BUSINESS_OPS = 'business_ops'


class MemoryScope(enum.StrEnum):
    SESSION = 'session'
    USER = 'user'
    ORG = 'org'
    NONE = 'none'


class EnvironmentKind(enum.StrEnum):
    SANDBOX = 'sandbox'
    STAGING = 'staging'
    PRODUCTION = 'production'


class DataSourceKind(enum.StrEnum):
    DATABASE = 'database'
    API = 'api'
    VECTOR_STORE = 'vector_store'
    STREAM = 'stream'


class SkillKind(enum.StrEnum):
    ATOMIC = 'atomic'
    COMPOSITE = 'composite'


class GraphNodeType(enum.StrEnum):
    AGENT = 'agent'
    SKILL = 'skill'
    TOOL = 'tool'


class DataBindingAccessMode(enum.StrEnum):
    READ = 'read'
    WRITE = 'write'
    READ_WRITE = 'read_write'


class PrincipalKind(enum.StrEnum):
    USER = 'user'
    AGENT = 'agent'
    SERVICE_ACCOUNT = 'service_account'


class PolicyEffect(enum.StrEnum):
    ALLOW = 'allow'
    DENY = 'deny'


class PolicyScopeType(enum.StrEnum):
    ENTITY = 'entity'
    INVOCATION = 'invocation'
    DATA_SCOPE = 'data_scope'
    ENVIRONMENT = 'environment'


class EvalRunStatus(enum.StrEnum):
    PENDING = 'pending'
    RUNNING = 'running'
    PASSED = 'passed'
    FAILED = 'failed'


class RealizingEntityType(enum.StrEnum):
    AGENT = 'agent'
    SKILL = 'skill'
    TOOL = 'tool'


class VersionedEntityKind(enum.StrEnum):
    AGENT = 'agent'
    SKILL = 'skill'
    TOOL = 'tool'
    CAPABILITY = 'capability'
    DATASOURCE = 'datasource'
    DATAPRODUCT = 'dataproduct'


class AuditDecision(enum.StrEnum):
    ALLOW = 'allow'
    DENY = 'deny'
```

```python
# src/loom/model/base.py
import datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all Loom registry ORM models."""


class TimestampMixin:
    """Adds server-tracked `created_at`/`updated_at` columns."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )
```

```python
# src/loom/model/tenant.py
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, TimestampMixin
from loom.model.enums import PrincipalKind


class Tenant(Base, TimestampMixin):
    """An isolated customer/organisation boundary; Environments and Principals scope to a Tenant."""

    __tablename__ = 'tenant'

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(sa.String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(sa.String(255))


class Principal(Base, TimestampMixin):
    """A user, agent, or service account that can own entities or hold a RoleBinding."""

    __tablename__ = 'principal'
    __table_args__ = (
        sa.UniqueConstraint('tenant_id', 'external_id', name='uq_principal_tenant_external_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    kind: Mapped[PrincipalKind] = mapped_column(
        sa.Enum(PrincipalKind, name='principal_kind', values_callable=lambda obj: [e.value for e in obj])
    )
    display_name: Mapped[str] = mapped_column(sa.String(255))
    external_id: Mapped[str] = mapped_column(sa.String(255))
```

```python
# src/loom/model/schemas/__init__.py
"""Pydantic wire schemas mirroring loom.model ORM classes."""
```

```python
# src/loom/model/schemas/tenant.py
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import PrincipalKind


class TenantCreate(BaseModel):
    slug: str
    name: str


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str


class TenantUpdate(BaseModel):
    name: str | None = None


class PrincipalCreate(BaseModel):
    tenant_id: uuid.UUID
    kind: PrincipalKind
    display_name: str
    external_id: str


class PrincipalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: PrincipalKind
    display_name: str
    external_id: str


class PrincipalUpdate(BaseModel):
    display_name: str | None = None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/model/test_tenant.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/loom/model tests
git commit -m "Add registry model foundations: enums, base mixins, Tenant/Principal"
```

---

## Task 2: VersionedEntityMixin and shared helpers

**Files:**
- Modify: `src/loom/model/base.py`
- Create: `src/loom/model/schemas/base.py`
- Test: `tests/model/test_versioned_entity_mixin.py`

**Interfaces:**
- Consumes: `loom.model.base.Base`, `TimestampMixin` (Task 1); `loom.model.tenant.Tenant`, `Principal` (Task 1).
- Produces: `loom.model.base.PortableJSON` (a `sa.JSON` variant that becomes `JSONB` on Postgres); `loom.model.base.enum_column(enum_cls, name) -> sa.Enum`; `loom.model.base.current_version_index(table_name) -> sa.Index`; `loom.model.base.exactly_one_of(*columns: str) -> str`; `loom.model.base.VersionedEntityMixin` with columns `id, entity_id, version, is_current, slug, name, description, lifecycle_state, maturity, classification, tenant_id, owner_id, created_by_id, created_at, approved_by_id, approved_at`; `loom.model.schemas.base.VersionedEntityCreate`, `VersionedEntityRead` (Pydantic mirrors, to be subclassed by every concrete entity's schemas in later tasks).

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_versioned_entity_mixin.py
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, VersionedEntityMixin, current_version_index, exactly_one_of
from loom.model.tenant import Principal, Tenant


class _Widget(Base, VersionedEntityMixin):
    __tablename__ = 'widget'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_widget_entity_version'),
        current_version_index('widget'),
    )


def _make_tenant_and_principal(session) -> tuple[Tenant, Principal]:
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()
    return tenant, principal


def test_versioned_entity_defaults_and_fk(session):
    tenant, principal = _make_tenant_and_principal(session)
    widget = _Widget(
        slug='w', name='Widget', tenant_id=tenant.id, owner_id=principal.id, created_by_id=principal.id
    )
    session.add(widget)
    session.commit()
    assert widget.version == 1
    assert widget.is_current is True
    assert widget.lifecycle_state == 'draft'


def test_only_one_current_row_per_entity_id(session):
    tenant, principal = _make_tenant_and_principal(session)
    entity_id = uuid.uuid4()
    session.add(
        _Widget(
            entity_id=entity_id,
            version=1,
            is_current=True,
            slug='w',
            name='Widget v1',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
        )
    )
    session.commit()
    session.add(
        _Widget(
            entity_id=entity_id,
            version=2,
            is_current=True,
            slug='w',
            name='Widget v2',
            tenant_id=tenant.id,
            owner_id=principal.id,
            created_by_id=principal.id,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()


def test_exactly_one_of_rejects_zero_and_two():
    assert exactly_one_of('a', 'b') == (
        '((a IS NOT NULL AND b IS NULL) OR (b IS NOT NULL AND a IS NULL))'
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_versioned_entity_mixin.py -v`
Expected: FAIL — `ImportError: cannot import name 'VersionedEntityMixin' from 'loom.model.base'`

- [ ] **Step 3: Implement**

Modify `src/loom/model/base.py` — add these imports and definitions (keep existing `Base`/`TimestampMixin`):

```python
import enum
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from loom.model.enums import Classification, LifecycleState, MaturityLevel

PortableJSON = sa.JSON().with_variant(JSONB(), 'postgresql')


def enum_column(enum_cls: type[enum.Enum], name: str) -> sa.Enum:
    """A SQLAlchemy Enum type that persists the member *value* (matches the wire schema), not its name."""
    return sa.Enum(enum_cls, name=name, values_callable=lambda obj: [e.value for e in obj])


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
        conditions = [f'{chosen} IS NOT NULL'] + [f'{other} IS NULL' for other in others]
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

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), default=uuid.uuid4, index=True)
    version: Mapped[int] = mapped_column(sa.Integer(), default=1)
    is_current: Mapped[bool] = mapped_column(sa.Boolean(), default=True)
    slug: Mapped[str] = mapped_column(sa.String(255), index=True)
    name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text(), default=None)
    lifecycle_state: Mapped[LifecycleState] = mapped_column(
        enum_column(LifecycleState, 'lifecycle_state'), default=LifecycleState.DRAFT
    )
    maturity: Mapped[MaturityLevel] = mapped_column(
        enum_column(MaturityLevel, 'maturity_level'), default=MaturityLevel.EXPERIMENTAL
    )
    classification: Mapped[Classification] = mapped_column(
        enum_column(Classification, 'classification'), default=Classification.INTERNAL
    )
    created_at: Mapped[uuid.UUID] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    approved_at: Mapped[uuid.UUID | None] = mapped_column(sa.DateTime(timezone=True), default=None)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'))
    created_by_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), default=None
    )
```

Note: `created_at`/`approved_at` are typed `Mapped[uuid.UUID]`/`Mapped[uuid.UUID | None]` above only as a copy-paste guard rail reminder — **fix the type hints to `datetime.datetime`** (add `import datetime` to the top of `base.py`) before running the tests:

```python
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    approved_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), default=None
    )
```

```python
# src/loom/model/schemas/base.py
import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import Classification, LifecycleState, MaturityLevel


class VersionedEntityCreate(BaseModel):
    """Shared input fields for creating a new version row of any versioned entity."""

    entity_id: uuid.UUID | None = None
    version: int = 1
    is_current: bool = True
    slug: str
    name: str
    description: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    maturity: MaturityLevel = MaturityLevel.EXPERIMENTAL
    classification: Classification = Classification.INTERNAL
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    created_by_id: uuid.UUID
    approved_by_id: uuid.UUID | None = None


class VersionedEntityRead(BaseModel):
    """Shared output fields for any versioned entity."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    version: int
    is_current: bool
    slug: str
    name: str
    description: str | None
    lifecycle_state: LifecycleState
    maturity: MaturityLevel
    classification: Classification
    created_at: datetime.datetime
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    created_by_id: uuid.UUID
    approved_by_id: uuid.UUID | None
    approved_at: datetime.datetime | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_versioned_entity_mixin.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add VersionedEntityMixin and shared model/schema helpers"
```

---

## Task 3: Environment

**Files:**
- Create: `src/loom/model/environment.py`
- Create: `src/loom/model/schemas/environment.py`
- Test: `tests/model/test_environment.py`

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `enum_column` (Task 1/2); `Tenant` (Task 1); `EnvironmentKind` (Task 1).
- Produces: `loom.model.environment.Environment(id, tenant_id, name, kind, compute_boundary_ref, network_boundary_ref)`; `loom.model.schemas.environment.EnvironmentCreate/Read/Update`.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_environment.py
import pytest
import sqlalchemy as sa

from loom.model.enums import EnvironmentKind
from loom.model.environment import Environment
from loom.model.schemas.environment import EnvironmentCreate, EnvironmentRead
from loom.model.tenant import Tenant


def test_environment_round_trip(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()

    env = Environment(
        **EnvironmentCreate(
            tenant_id=tenant.id,
            name='prod',
            kind=EnvironmentKind.PRODUCTION,
            compute_boundary_ref='vpc-prod-compute',
            network_boundary_ref='vpc-prod-net',
        ).model_dump()
    )
    session.add(env)
    session.commit()

    read = EnvironmentRead.model_validate(env)
    assert read.kind == EnvironmentKind.PRODUCTION


def test_environment_name_unique_per_tenant(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()

    session.add(
        Environment(
            tenant_id=tenant.id,
            name='prod',
            kind=EnvironmentKind.PRODUCTION,
            compute_boundary_ref='a',
            network_boundary_ref='b',
        )
    )
    session.commit()
    session.add(
        Environment(
            tenant_id=tenant.id,
            name='prod',
            kind=EnvironmentKind.SANDBOX,
            compute_boundary_ref='c',
            network_boundary_ref='d',
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_environment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.environment'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/environment.py
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, TimestampMixin, enum_column
from loom.model.enums import EnvironmentKind


class Environment(Base, TimestampMixin):
    """An isolated execution context (Sandbox/Staging/Production) with its own compute/network boundary."""

    __tablename__ = 'environment'
    __table_args__ = (sa.UniqueConstraint('tenant_id', 'name', name='uq_environment_tenant_name'),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    name: Mapped[str] = mapped_column(sa.String(255))
    kind: Mapped[EnvironmentKind] = mapped_column(enum_column(EnvironmentKind, 'environment_kind'))
    compute_boundary_ref: Mapped[str] = mapped_column(sa.String(255))
    network_boundary_ref: Mapped[str] = mapped_column(sa.String(255))
```

```python
# src/loom/model/schemas/environment.py
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import EnvironmentKind


class EnvironmentCreate(BaseModel):
    tenant_id: uuid.UUID
    name: str
    kind: EnvironmentKind
    compute_boundary_ref: str
    network_boundary_ref: str


class EnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    kind: EnvironmentKind
    compute_boundary_ref: str
    network_boundary_ref: str


class EnvironmentUpdate(BaseModel):
    compute_boundary_ref: str | None = None
    network_boundary_ref: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_environment.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add Environment model and schemas"
```

---

## Task 4: Agent

**Files:**
- Create: `src/loom/model/agent.py`
- Create: `src/loom/model/schemas/agent.py`
- Test: `tests/model/test_agent.py`

**Interfaces:**
- Consumes: `VersionedEntityMixin`, `PortableJSON`, `enum_column`, `current_version_index` (Task 2); `Layer`, `MemoryScope` (Task 1); `VersionedEntityCreate`/`VersionedEntityRead` (Task 2).
- Produces: `loom.model.agent.Agent(layer, llm_config, prompt, memory_scope, permission_boundary)` (plus all `VersionedEntityMixin` columns); `loom.model.schemas.agent.AgentCreate/AgentRead`.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_agent.py
from loom.model.agent import Agent
from loom.model.enums import Layer, MemoryScope
from loom.model.schemas.agent import AgentCreate, AgentRead
from loom.model.tenant import Principal, Tenant


def _tenant_and_principal(session) -> tuple[Tenant, Principal]:
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()
    return tenant, principal


def test_agent_round_trip(session):
    tenant, principal = _tenant_and_principal(session)
    payload = AgentCreate(
        slug='triage-agent',
        name='Triage Agent',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        layer=Layer.BUSINESS_OPS,
        llm_config={'provider': 'anthropic', 'model': 'claude-opus-5'},
        prompt='You triage incoming tickets.',
        memory_scope=MemoryScope.SESSION,
    )
    agent = Agent(**payload.model_dump())
    session.add(agent)
    session.commit()

    read = AgentRead.model_validate(agent)
    assert read.layer == Layer.BUSINESS_OPS
    assert read.llm_config['model'] == 'claude-opus-5'
    assert read.permission_boundary == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.agent'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/agent.py
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, PortableJSON, VersionedEntityMixin, current_version_index, enum_column
from loom.model.enums import Layer, MemoryScope


class Agent(Base, VersionedEntityMixin):
    """A non-deterministic, autonomous reasoning unit: model config, versioned prompt, memory scope, permission boundary."""

    __tablename__ = 'agent'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_agent_entity_version'),
        current_version_index('agent'),
    )

    layer: Mapped[Layer] = mapped_column(enum_column(Layer, 'layer'))
    llm_config: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    prompt: Mapped[str] = mapped_column(sa.Text())
    memory_scope: Mapped[MemoryScope] = mapped_column(enum_column(MemoryScope, 'memory_scope'))
    permission_boundary: Mapped[dict] = mapped_column(PortableJSON, default=dict)
```

```python
# src/loom/model/schemas/agent.py
from loom.model.enums import Layer, MemoryScope
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class AgentCreate(VersionedEntityCreate):
    layer: Layer
    llm_config: dict
    prompt: str
    memory_scope: MemoryScope
    permission_boundary: dict = {}


class AgentRead(VersionedEntityRead):
    layer: Layer
    llm_config: dict
    prompt: str
    memory_scope: MemoryScope
    permission_boundary: dict
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_agent.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add Agent model and schemas"
```

---

## Task 5: Tool

**Files:**
- Create: `src/loom/model/tool.py`
- Create: `src/loom/model/schemas/tool.py`
- Test: `tests/model/test_tool.py`

**Interfaces:**
- Consumes: same Task 2 helpers as Task 4.
- Produces: `loom.model.tool.Tool(invocation_spec, auth_binding_id)`; `loom.model.schemas.tool.ToolCreate/ToolRead`. (`ToolDataBinding` is added to this same file in Task 7, once `DataSource`/`DataProduct` exist.)

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_tool.py
from loom.model.schemas.tool import ToolCreate, ToolRead
from loom.model.tenant import Principal, Tenant
from loom.model.tool import Tool


def test_tool_round_trip(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()

    payload = ToolCreate(
        slug='send-email',
        name='Send Email',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        invocation_spec={'method': 'POST', 'path': '/v1/email'},
    )
    tool = Tool(**payload.model_dump())
    session.add(tool)
    session.commit()

    read = ToolRead.model_validate(tool)
    assert read.invocation_spec['method'] == 'POST'
    assert read.auth_binding_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.tool'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/tool.py
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, PortableJSON, VersionedEntityMixin, current_version_index


class Tool(Base, VersionedEntityMixin):
    """Deterministic automation: a static, design-time-bound external action interface."""

    __tablename__ = 'tool'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_tool_entity_version'),
        current_version_index('tool'),
    )

    invocation_spec: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    auth_binding_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)
```

```python
# src/loom/model/schemas/tool.py
import uuid

from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class ToolCreate(VersionedEntityCreate):
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None = None


class ToolRead(VersionedEntityRead):
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_tool.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add Tool model and schemas"
```

---

## Task 6: DataSource

**Files:**
- Create: `src/loom/model/datasource.py`
- Create: `src/loom/model/schemas/datasource.py`
- Test: `tests/model/test_datasource.py`

**Interfaces:**
- Consumes: same Task 2 helpers; `DataSourceKind` (Task 1).
- Produces: `loom.model.datasource.DataSource(kind, connection_binding_id)`; `loom.model.schemas.datasource.DataSourceCreate/Read`.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_datasource.py
from loom.model.datasource import DataSource
from loom.model.enums import DataSourceKind
from loom.model.schemas.datasource import DataSourceCreate, DataSourceRead
from loom.model.tenant import Principal, Tenant


def test_datasource_round_trip(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()

    payload = DataSourceCreate(
        slug='orders-db',
        name='Orders DB',
        tenant_id=tenant.id,
        owner_id=principal.id,
        created_by_id=principal.id,
        kind=DataSourceKind.DATABASE,
    )
    datasource = DataSource(**payload.model_dump())
    session.add(datasource)
    session.commit()

    read = DataSourceRead.model_validate(datasource)
    assert read.kind == DataSourceKind.DATABASE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_datasource.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.datasource'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/datasource.py
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, VersionedEntityMixin, current_version_index, enum_column
from loom.model.enums import DataSourceKind


class DataSource(Base, VersionedEntityMixin):
    """Governed raw data connection (DB, API, vector store, stream)."""

    __tablename__ = 'datasource'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_datasource_entity_version'),
        current_version_index('datasource'),
    )

    kind: Mapped[DataSourceKind] = mapped_column(enum_column(DataSourceKind, 'datasource_kind'))
    connection_binding_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)
```

```python
# src/loom/model/schemas/datasource.py
import uuid

from loom.model.enums import DataSourceKind
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class DataSourceCreate(VersionedEntityCreate):
    kind: DataSourceKind
    connection_binding_id: uuid.UUID | None = None


class DataSourceRead(VersionedEntityRead):
    kind: DataSourceKind
    connection_binding_id: uuid.UUID | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_datasource.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add DataSource model and schemas"
```

---

## Task 7: DataProduct, DataProductLineage, ToolDataBinding

**Files:**
- Create: `src/loom/model/dataproduct.py`
- Create: `src/loom/model/schemas/dataproduct.py`
- Modify: `src/loom/model/tool.py` (add `ToolDataBinding`)
- Modify: `src/loom/model/schemas/tool.py` (add `ToolDataBindingCreate`/`Read`)
- Test: `tests/model/test_dataproduct.py`

**Interfaces:**
- Consumes: `DataSource` (Task 6), `Tool` (Task 5), Task 2 helpers (`exactly_one_of`, `enum_column`), `DataBindingAccessMode` (Task 1).
- Produces: `loom.model.dataproduct.DataProduct(contract)`, `loom.model.dataproduct.DataProductLineage(dataproduct_id, source_datasource_id, source_dataproduct_id)`; `loom.model.tool.ToolDataBinding(tool_id, datasource_id, dataproduct_id, access_mode)`; matching schemas.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_dataproduct.py
import pytest
import sqlalchemy as sa

from loom.model.dataproduct import DataProduct, DataProductLineage
from loom.model.datasource import DataSource
from loom.model.enums import DataBindingAccessMode, DataSourceKind
from loom.model.schemas.dataproduct import DataProductCreate, DataProductRead
from loom.model.schemas.tool import ToolCreate, ToolDataBindingCreate
from loom.model.tenant import Principal, Tenant
from loom.model.tool import Tool, ToolDataBinding


def _tenant_and_principal(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()
    return tenant, principal


def test_dataproduct_and_lineage(session):
    tenant, principal = _tenant_and_principal(session)
    datasource = DataSource(
        slug='orders-db', name='Orders DB', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, kind=DataSourceKind.DATABASE,
    )
    session.add(datasource)
    session.commit()

    payload = DataProductCreate(
        slug='orders-curated', name='Curated Orders', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, contract={'columns': ['order_id', 'total']},
    )
    dataproduct = DataProduct(**payload.model_dump())
    session.add(dataproduct)
    session.commit()

    session.add(DataProductLineage(dataproduct_id=dataproduct.id, source_datasource_id=datasource.id))
    session.commit()

    read = DataProductRead.model_validate(dataproduct)
    assert read.contract['columns'] == ['order_id', 'total']

    with pytest.raises(sa.exc.IntegrityError):
        session.add(DataProductLineage(dataproduct_id=dataproduct.id))
        session.commit()


def test_tool_data_binding_is_version_pinned_and_exclusive(session):
    tenant, principal = _tenant_and_principal(session)
    datasource = DataSource(
        slug='orders-db', name='Orders DB', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, kind=DataSourceKind.DATABASE,
    )
    tool = Tool(**ToolCreate(
        slug='query-orders', name='Query Orders', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, invocation_spec={},
    ).model_dump())
    session.add_all([datasource, tool])
    session.commit()

    binding = ToolDataBinding(
        **ToolDataBindingCreate(
            tool_id=tool.id, datasource_id=datasource.id, access_mode=DataBindingAccessMode.READ
        ).model_dump()
    )
    session.add(binding)
    session.commit()
    assert binding.datasource_id == datasource.id

    with pytest.raises(sa.exc.IntegrityError):
        session.add(ToolDataBinding(tool_id=tool.id, access_mode=DataBindingAccessMode.READ))
        session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_dataproduct.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.dataproduct'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/dataproduct.py
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
    """A curated, contract-bearing, versioned publication over one or more DataSources."""

    __tablename__ = 'dataproduct'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_dataproduct_entity_version'),
        current_version_index('dataproduct'),
    )

    contract: Mapped[dict] = mapped_column(PortableJSON, default=dict)


class DataProductLineage(Base, TimestampMixin):
    """One upstream source (a DataSource or another DataProduct) feeding a DataProduct version."""

    __tablename__ = 'dataproduct_lineage'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of('source_datasource_id', 'source_dataproduct_id'),
            name='ck_dataproduct_lineage_one_source',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    dataproduct_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('dataproduct.id'), index=True)
    source_datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('datasource.id'), default=None
    )
    source_dataproduct_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('dataproduct.id'), default=None
    )
```

```python
# src/loom/model/schemas/dataproduct.py
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class DataProductCreate(VersionedEntityCreate):
    contract: dict


class DataProductRead(VersionedEntityRead):
    contract: dict


class DataProductLineageCreate(BaseModel):
    dataproduct_id: uuid.UUID
    source_datasource_id: uuid.UUID | None = None
    source_dataproduct_id: uuid.UUID | None = None


class DataProductLineageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataproduct_id: uuid.UUID
    source_datasource_id: uuid.UUID | None
    source_dataproduct_id: uuid.UUID | None
```

Modify `src/loom/model/tool.py` — add imports and the new class:

```python
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
```

```python
class ToolDataBinding(Base, TimestampMixin):
    """Static, compiler-checked, version-pinned binding of a Tool to a DataSource or DataProduct version."""

    __tablename__ = 'tool_data_binding'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of('datasource_id', 'dataproduct_id'), name='ck_tool_data_binding_one_target'
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tool_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tool.id'), index=True)
    datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('datasource.id'), default=None
    )
    dataproduct_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('dataproduct.id'), default=None
    )
    access_mode: Mapped[DataBindingAccessMode] = mapped_column(
        enum_column(DataBindingAccessMode, 'data_binding_access_mode')
    )
```

Modify `src/loom/model/schemas/tool.py` — append:

```python
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import DataBindingAccessMode


class ToolDataBindingCreate(BaseModel):
    tool_id: uuid.UUID
    datasource_id: uuid.UUID | None = None
    dataproduct_id: uuid.UUID | None = None
    access_mode: DataBindingAccessMode


class ToolDataBindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_id: uuid.UUID
    datasource_id: uuid.UUID | None
    dataproduct_id: uuid.UUID | None
    access_mode: DataBindingAccessMode
```

(Merge the new `import uuid`/`pydantic` imports into the existing import block at the top of the file rather than duplicating them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_dataproduct.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add DataProduct, DataProductLineage, and ToolDataBinding"
```

---

## Task 8: Skill, SkillGraphNode, SkillGraphEdge

**Files:**
- Create: `src/loom/model/skill.py`
- Create: `src/loom/model/schemas/skill.py`
- Test: `tests/model/test_skill.py`

**Interfaces:**
- Consumes: `Agent` (Task 4), `Tool` (Task 5), Task 2 helpers.
- Produces: `loom.model.skill.Skill(layer, kind, is_entry_point, atomic_content)`, `SkillGraphNode(skill_id, node_key, node_type, agent_id, skill_ref_id, tool_id, position)`, `SkillGraphEdge(skill_id, from_node_id, to_node_id)`; matching schemas.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_skill.py
import pytest
import sqlalchemy as sa

from loom.model.agent import Agent
from loom.model.enums import GraphNodeType, Layer, MemoryScope, SkillKind
from loom.model.schemas.agent import AgentCreate
from loom.model.schemas.skill import SkillCreate
from loom.model.skill import Skill, SkillGraphEdge, SkillGraphNode
from loom.model.tenant import Principal, Tenant


def _tenant_and_principal(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()
    return tenant, principal


def test_atomic_skill_requires_content(session):
    tenant, principal = _tenant_and_principal(session)
    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            Skill(
                slug='draft-reply', name='Draft Reply', tenant_id=tenant.id, owner_id=principal.id,
                created_by_id=principal.id, layer=Layer.BUSINESS_OPS, kind=SkillKind.ATOMIC,
                atomic_content=None,
            )
        )
        session.commit()


def test_composite_skill_graph(session):
    tenant, principal = _tenant_and_principal(session)
    agent = Agent(**AgentCreate(
        slug='triage-agent', name='Triage Agent', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, layer=Layer.BUSINESS_OPS, llm_config={}, prompt='p',
        memory_scope=MemoryScope.NONE,
    ).model_dump())
    session.add(agent)
    session.commit()

    skill = Skill(**SkillCreate(
        slug='triage-flow', name='Triage Flow', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, layer=Layer.BUSINESS_OPS, kind=SkillKind.COMPOSITE,
        is_entry_point=True,
    ).model_dump())
    session.add(skill)
    session.commit()

    start = SkillGraphNode(skill_id=skill.id, node_key='start', node_type=GraphNodeType.AGENT, agent_id=agent.id)
    session.add(start)
    session.commit()

    session.add(SkillGraphEdge(skill_id=skill.id, from_node_id=start.id, to_node_id=start.id))
    session.commit()

    assert session.query(SkillGraphEdge).count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_skill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.skill'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/skill.py
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
from loom.model.enums import GraphNodeType, Layer, SkillKind


class Skill(Base, VersionedEntityMixin):
    """Composable capability unit: atomic (prompt/code) or composite (a graph of Agent/Skill/Tool nodes)."""

    __tablename__ = 'skill'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_skill_entity_version'),
        current_version_index('skill'),
        sa.CheckConstraint(
            "(kind = 'atomic' AND atomic_content IS NOT NULL) OR (kind = 'composite' AND atomic_content IS NULL)",
            name='ck_skill_atomic_content_matches_kind',
        ),
    )

    layer: Mapped[Layer] = mapped_column(enum_column(Layer, 'layer'))
    kind: Mapped[SkillKind] = mapped_column(enum_column(SkillKind, 'skill_kind'))
    is_entry_point: Mapped[bool] = mapped_column(sa.Boolean(), default=False)
    atomic_content: Mapped[dict | None] = mapped_column(PortableJSON, default=None)


class SkillGraphNode(Base, TimestampMixin):
    """One node (Agent/Skill/Tool reference) within a composite Skill's graph."""

    __tablename__ = 'skill_graph_node'
    __table_args__ = (
        sa.UniqueConstraint('skill_id', 'node_key', name='uq_skill_graph_node_key'),
        sa.CheckConstraint(
            exactly_one_of('agent_id', 'skill_ref_id', 'tool_id'), name='ck_skill_graph_node_one_ref'
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('skill.id'), index=True)
    node_key: Mapped[str] = mapped_column(sa.String(255))
    node_type: Mapped[GraphNodeType] = mapped_column(enum_column(GraphNodeType, 'graph_node_type'))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('agent.id'), default=None)
    skill_ref_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('skill.id'), default=None)
    tool_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('tool.id'), default=None)
    position: Mapped[dict | None] = mapped_column(PortableJSON, default=None)


class SkillGraphEdge(Base, TimestampMixin):
    """A directed edge between two nodes of the same composite Skill's graph."""

    __tablename__ = 'skill_graph_edge'
    __table_args__ = (
        sa.UniqueConstraint('skill_id', 'from_node_id', 'to_node_id', name='uq_skill_graph_edge'),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('skill.id'), index=True)
    from_node_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('skill_graph_node.id'))
    to_node_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('skill_graph_node.id'))
```

```python
# src/loom/model/schemas/skill.py
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import GraphNodeType, Layer, SkillKind
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class SkillCreate(VersionedEntityCreate):
    layer: Layer
    kind: SkillKind
    is_entry_point: bool = False
    atomic_content: dict | None = None


class SkillRead(VersionedEntityRead):
    layer: Layer
    kind: SkillKind
    is_entry_point: bool
    atomic_content: dict | None


class SkillGraphNodeCreate(BaseModel):
    skill_id: uuid.UUID
    node_key: str
    node_type: GraphNodeType
    agent_id: uuid.UUID | None = None
    skill_ref_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    position: dict | None = None


class SkillGraphNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    node_key: str
    node_type: GraphNodeType
    agent_id: uuid.UUID | None
    skill_ref_id: uuid.UUID | None
    tool_id: uuid.UUID | None
    position: dict | None


class SkillGraphEdgeCreate(BaseModel):
    skill_id: uuid.UUID
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID


class SkillGraphEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_skill.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add Skill, SkillGraphNode, and SkillGraphEdge"
```

---

## Task 9: Capability, CapabilityRealization

**Files:**
- Create: `src/loom/model/capability.py`
- Create: `src/loom/model/schemas/capability.py`
- Test: `tests/model/test_capability.py`

**Interfaces:**
- Consumes: `Agent` (4), `Skill` (8), `Tool` (5), Task 2 helpers, `RealizingEntityType` (Task 1).
- Produces: `loom.model.capability.Capability(target_metrics)`, `CapabilityRealization(capability_id, realizing_entity_type, realizing_agent_id, realizing_skill_id, realizing_tool_id, contribution_weight)`; matching schemas.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_capability.py
import decimal

import pytest
import sqlalchemy as sa

from loom.model.agent import Agent
from loom.model.capability import Capability, CapabilityRealization
from loom.model.enums import Layer, MemoryScope, RealizingEntityType
from loom.model.schemas.agent import AgentCreate
from loom.model.schemas.capability import CapabilityCreate, CapabilityRealizationCreate
from loom.model.tenant import Principal, Tenant


def test_capability_realization_exactly_one_realizer(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()

    agent = Agent(**AgentCreate(
        slug='triage-agent', name='Triage Agent', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, layer=Layer.BUSINESS_OPS, llm_config={}, prompt='p',
        memory_scope=MemoryScope.NONE,
    ).model_dump())
    session.add(agent)
    session.commit()

    capability = Capability(**CapabilityCreate(
        slug='ticket-triage', name='Ticket Triage', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, target_metrics=[{'metric_name': 'accuracy', 'target_value': 0.9}],
    ).model_dump())
    session.add(capability)
    session.commit()

    realization = CapabilityRealization(
        **CapabilityRealizationCreate(
            capability_id=capability.id, realizing_entity_type=RealizingEntityType.AGENT,
            realizing_agent_id=agent.id, contribution_weight=decimal.Decimal('1.0'),
        ).model_dump()
    )
    session.add(realization)
    session.commit()
    assert realization.contribution_weight == decimal.Decimal('1.0')

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            CapabilityRealization(
                capability_id=capability.id, realizing_entity_type=RealizingEntityType.AGENT,
                realizing_agent_id=agent.id, realizing_skill_id=None,
                contribution_weight=decimal.Decimal('1.0'),
            )
        )
        # Force the violation explicitly since both agent_id set alone is valid;
        # exercise the zero-set case instead:
        session.rollback()
        session.add(
            CapabilityRealization(
                capability_id=capability.id, realizing_entity_type=RealizingEntityType.AGENT,
                contribution_weight=decimal.Decimal('1.0'),
            )
        )
        session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_capability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.capability'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/capability.py
import decimal
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
from loom.model.enums import RealizingEntityType


class Capability(Base, VersionedEntityMixin):
    """First-class, versioned business-meaning concept; defines target metrics; all other entities realize it."""

    __tablename__ = 'capability'
    __table_args__ = (
        sa.UniqueConstraint('entity_id', 'version', name='uq_capability_entity_version'),
        current_version_index('capability'),
    )

    target_metrics: Mapped[list] = mapped_column(PortableJSON, default=list)


class CapabilityRealization(Base, TimestampMixin):
    """Join record: an Agent/Skill/Tool version realizes a Capability version, weighted."""

    __tablename__ = 'capability_realization'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of('realizing_agent_id', 'realizing_skill_id', 'realizing_tool_id'),
            name='ck_capability_realization_one_realizer',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    capability_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('capability.id'), index=True)
    realizing_entity_type: Mapped[RealizingEntityType] = mapped_column(
        enum_column(RealizingEntityType, 'realizing_entity_type')
    )
    realizing_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('agent.id'), default=None
    )
    realizing_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), default=None
    )
    realizing_tool_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), default=None
    )
    contribution_weight: Mapped[decimal.Decimal] = mapped_column(sa.Numeric(5, 4))
```

```python
# src/loom/model/schemas/capability.py
import decimal
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import RealizingEntityType
from loom.model.schemas.base import VersionedEntityCreate, VersionedEntityRead


class CapabilityCreate(VersionedEntityCreate):
    target_metrics: list[dict] = []


class CapabilityRead(VersionedEntityRead):
    target_metrics: list[dict]


class CapabilityRealizationCreate(BaseModel):
    capability_id: uuid.UUID
    realizing_entity_type: RealizingEntityType
    realizing_agent_id: uuid.UUID | None = None
    realizing_skill_id: uuid.UUID | None = None
    realizing_tool_id: uuid.UUID | None = None
    contribution_weight: decimal.Decimal


class CapabilityRealizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    capability_id: uuid.UUID
    realizing_entity_type: RealizingEntityType
    realizing_agent_id: uuid.UUID | None
    realizing_skill_id: uuid.UUID | None
    realizing_tool_id: uuid.UUID | None
    contribution_weight: decimal.Decimal
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_capability.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add Capability and CapabilityRealization"
```

---

## Task 10: Metric (rollup observability)

**Files:**
- Create: `src/loom/model/observability.py`
- Create: `src/loom/model/schemas/observability.py`
- Test: `tests/model/test_observability.py`

**Interfaces:**
- Consumes: `Tenant` (1), `Capability` (9), `Agent` (4), `Skill` (8), `Tool` (5), Task 2 helpers.
- Produces: `loom.model.observability.Metric(tenant_id, capability_id, agent_id, skill_id, tool_id, metric_name, value, unit, is_realization_score, weakest_contributor_*_id, otel_resource_ref, computed_at)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_observability.py
import decimal

import pytest
import sqlalchemy as sa

from loom.model.capability import Capability
from loom.model.observability import Metric
from loom.model.schemas.capability import CapabilityCreate
from loom.model.schemas.observability import MetricCreate
from loom.model.tenant import Principal, Tenant


def test_metric_scopes_to_exactly_one_entity(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()

    capability = Capability(**CapabilityCreate(
        slug='ticket-triage', name='Ticket Triage', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id,
    ).model_dump())
    session.add(capability)
    session.commit()

    metric = Metric(**MetricCreate(
        tenant_id=tenant.id, capability_id=capability.id, metric_name='realization_score',
        value=decimal.Decimal('0.87'), is_realization_score=True,
    ).model_dump())
    session.add(metric)
    session.commit()
    assert metric.value == decimal.Decimal('0.870000')

    with pytest.raises(sa.exc.IntegrityError):
        session.add(Metric(tenant_id=tenant.id, metric_name='orphan', value=decimal.Decimal('1')))
        session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_observability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.observability'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/observability.py
import datetime
import decimal
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, TimestampMixin, exactly_one_of


class Metric(Base, TimestampMixin):
    """
    An observable signal, pipeline-derived. Scopes to exactly one of Capability/
    Agent/Skill/Tool. This is the rollup row only (e.g. `realization_score`) —
    raw spans and time-series points live in the separate OTel-backed Trace/
    Metrics store, not here.
    """

    __tablename__ = 'metric'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of('capability_id', 'agent_id', 'skill_id', 'tool_id'), name='ck_metric_one_scope'
        ),
        sa.CheckConstraint(
            '(weakest_contributor_agent_id IS NULL AND weakest_contributor_skill_id IS NULL '
            'AND weakest_contributor_tool_id IS NULL) OR '
            + exactly_one_of(
                'weakest_contributor_agent_id', 'weakest_contributor_skill_id', 'weakest_contributor_tool_id'
            ),
            name='ck_metric_weakest_contributor_at_most_one',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    capability_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('capability.id'), default=None
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('agent.id'), default=None)
    skill_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('skill.id'), default=None)
    tool_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('tool.id'), default=None)
    metric_name: Mapped[str] = mapped_column(sa.String(255))
    value: Mapped[decimal.Decimal] = mapped_column(sa.Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    is_realization_score: Mapped[bool] = mapped_column(sa.Boolean(), default=False)
    weakest_contributor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('agent.id'), default=None
    )
    weakest_contributor_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('skill.id'), default=None
    )
    weakest_contributor_tool_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('tool.id'), default=None
    )
    otel_resource_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
```

```python
# src/loom/model/schemas/observability.py
import decimal
import uuid

from pydantic import BaseModel, ConfigDict


class MetricCreate(BaseModel):
    tenant_id: uuid.UUID
    capability_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    skill_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    metric_name: str
    value: decimal.Decimal
    unit: str | None = None
    is_realization_score: bool = False
    weakest_contributor_agent_id: uuid.UUID | None = None
    weakest_contributor_skill_id: uuid.UUID | None = None
    weakest_contributor_tool_id: uuid.UUID | None = None
    otel_resource_ref: str | None = None


class MetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    capability_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    skill_id: uuid.UUID | None
    tool_id: uuid.UUID | None
    metric_name: str
    value: decimal.Decimal
    unit: str | None
    is_realization_score: bool
    weakest_contributor_agent_id: uuid.UUID | None
    weakest_contributor_skill_id: uuid.UUID | None
    weakest_contributor_tool_id: uuid.UUID | None
    otel_resource_ref: str | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_observability.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add rollup Metric model and schemas"
```

---

## Task 11: Governance — Policy, RoleBinding, AuditEvent

**Files:**
- Create: `src/loom/model/governance.py`
- Create: `src/loom/model/schemas/governance.py`
- Test: `tests/model/test_governance.py`

**Interfaces:**
- Consumes: `Tenant`, `Principal` (1), `Environment` (3), Task 2 helpers, `PolicyEffect`/`PolicyScopeType`/`AuditDecision` (Task 1).
- Produces: `loom.model.governance.Policy(name, description, effect, scope_type, rule, created_by_id)`, `RoleBinding(principal_id, role, scope_type, scope_ref, environment_id, delegated_from_principal_id, permission_subset)`, `AuditEvent(occurred_at, actor_principal_id, acting_as_principal_id, action, entity_type, entity_id, environment_id, decision, policy_id, details)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_governance.py
import pytest
import sqlalchemy as sa

from loom.model.enums import AuditDecision, PolicyEffect, PolicyScopeType
from loom.model.governance import AuditEvent, Policy, RoleBinding
from loom.model.schemas.governance import AuditEventCreate, PolicyCreate, RoleBindingCreate
from loom.model.tenant import Principal, Tenant


def _tenant_and_two_principals(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    grantor = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    subject = Principal(tenant_id=tenant.id, kind='agent', display_name='Bot', external_id='bot')
    session.add_all([grantor, subject])
    session.commit()
    return tenant, grantor, subject


def test_delegated_role_binding_requires_permission_subset(session):
    tenant, grantor, subject = _tenant_and_two_principals(session)
    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            RoleBinding(
                tenant_id=tenant.id, principal_id=subject.id, role='reader',
                scope_type=PolicyScopeType.ENTITY, scope_ref={}, delegated_from_principal_id=grantor.id,
                permission_subset=None, created_by_id=grantor.id,
            )
        )
        session.commit()


def test_policy_role_binding_audit_round_trip(session):
    tenant, grantor, subject = _tenant_and_two_principals(session)
    policy = Policy(**PolicyCreate(
        tenant_id=tenant.id, name='Deny write outside sandbox', effect=PolicyEffect.DENY,
        scope_type=PolicyScopeType.ENVIRONMENT, rule={'condition': 'env != sandbox'}, created_by_id=grantor.id,
    ).model_dump())
    session.add(policy)
    session.commit()

    binding = RoleBinding(**RoleBindingCreate(
        tenant_id=tenant.id, principal_id=subject.id, role='reader', scope_type=PolicyScopeType.ENTITY,
        scope_ref={'entity_id': str(subject.id)}, created_by_id=grantor.id,
    ).model_dump())
    session.add(binding)
    session.commit()

    event = AuditEvent(**AuditEventCreate(
        tenant_id=tenant.id, actor_principal_id=subject.id, action='invoke', entity_type='tool',
        decision=AuditDecision.DENY, policy_id=policy.id, details={'reason': 'outside sandbox'},
    ).model_dump())
    session.add(event)
    session.commit()
    assert event.decision == AuditDecision.DENY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_governance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.governance'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/governance.py
import datetime
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, PortableJSON, TimestampMixin, enum_column
from loom.model.enums import AuditDecision, PolicyEffect, PolicyScopeType


class Policy(Base, TimestampMixin):
    """A governance rule: effect (allow/deny) over a scope, evaluated by the runtime Policy Engine."""

    __tablename__ = 'policy'

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text(), default=None)
    effect: Mapped[PolicyEffect] = mapped_column(enum_column(PolicyEffect, 'policy_effect'))
    scope_type: Mapped[PolicyScopeType] = mapped_column(enum_column(PolicyScopeType, 'policy_scope_type'))
    rule: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    created_by_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'))


class RoleBinding(Base, TimestampMixin):
    """Grants a Principal a role over a scope. Delegated bindings are constrained via `permission_subset`."""

    __tablename__ = 'role_binding'
    __table_args__ = (
        sa.CheckConstraint(
            'delegated_from_principal_id IS NULL OR permission_subset IS NOT NULL',
            name='ck_role_binding_delegation_requires_subset',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    principal_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'), index=True)
    role: Mapped[str] = mapped_column(sa.String(255))
    scope_type: Mapped[PolicyScopeType] = mapped_column(enum_column(PolicyScopeType, 'policy_scope_type'))
    scope_ref: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('environment.id'), default=None
    )
    delegated_from_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), default=None
    )
    permission_subset: Mapped[dict | None] = mapped_column(PortableJSON, default=None)
    created_by_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'))


class AuditEvent(Base):
    """
    Immutable audit trail entry, separate from operational Trace storage per
    the compliance-vs-operational retention split. Append-only by convention:
    no Update/Delete schema is exposed in `schemas/governance.py`.
    """

    __tablename__ = 'audit_event'

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    actor_principal_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'))
    acting_as_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('principal.id'), default=None
    )
    action: Mapped[str] = mapped_column(sa.String(255))
    entity_type: Mapped[str] = mapped_column(sa.String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), default=None)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('environment.id'), default=None
    )
    decision: Mapped[AuditDecision] = mapped_column(enum_column(AuditDecision, 'audit_decision'))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('policy.id'), default=None)
    details: Mapped[dict] = mapped_column(PortableJSON, default=dict)
```

```python
# src/loom/model/schemas/governance.py
import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import AuditDecision, PolicyEffect, PolicyScopeType


class PolicyCreate(BaseModel):
    tenant_id: uuid.UUID
    name: str
    description: str | None = None
    effect: PolicyEffect
    scope_type: PolicyScopeType
    rule: dict
    created_by_id: uuid.UUID


class PolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    effect: PolicyEffect
    scope_type: PolicyScopeType
    rule: dict
    created_by_id: uuid.UUID


class PolicyUpdate(BaseModel):
    description: str | None = None
    rule: dict | None = None


class RoleBindingCreate(BaseModel):
    tenant_id: uuid.UUID
    principal_id: uuid.UUID
    role: str
    scope_type: PolicyScopeType
    scope_ref: dict
    environment_id: uuid.UUID | None = None
    delegated_from_principal_id: uuid.UUID | None = None
    permission_subset: dict | None = None
    created_by_id: uuid.UUID


class RoleBindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    principal_id: uuid.UUID
    role: str
    scope_type: PolicyScopeType
    scope_ref: dict
    environment_id: uuid.UUID | None
    delegated_from_principal_id: uuid.UUID | None
    permission_subset: dict | None
    created_by_id: uuid.UUID


class RoleBindingUpdate(BaseModel):
    role: str | None = None
    permission_subset: dict | None = None


class AuditEventCreate(BaseModel):
    tenant_id: uuid.UUID
    actor_principal_id: uuid.UUID
    acting_as_principal_id: uuid.UUID | None = None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    decision: AuditDecision
    policy_id: uuid.UUID | None = None
    details: dict = {}


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    occurred_at: datetime.datetime
    actor_principal_id: uuid.UUID
    acting_as_principal_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    decision: AuditDecision
    policy_id: uuid.UUID | None
    details: dict
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_governance.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add Policy, RoleBinding, and AuditEvent"
```

---

## Task 12: Evaluation — EvalSuite, EvalRun

**Files:**
- Create: `src/loom/model/evaluation.py`
- Create: `src/loom/model/schemas/evaluation.py`
- Test: `tests/model/test_evaluation.py`

**Interfaces:**
- Consumes: all six versioned entity tables (Tasks 4-9), `Tenant`/`Principal` (1), Task 2 helpers, `VersionedEntityKind`/`EvalRunStatus`/`LifecycleState` (Task 1).
- Produces: `loom.model.evaluation.EvalSuite(slug, name, description, target_entity_type, criteria, created_by_id)`, `EvalRun(eval_suite_id, target_entity_type, target_agent_id..target_dataproduct_id, status, started_at, completed_at, results, triggered_by_id, gates_transition_to)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_evaluation.py
import pytest
import sqlalchemy as sa

from loom.model.agent import Agent
from loom.model.enums import EvalRunStatus, Layer, LifecycleState, MemoryScope, VersionedEntityKind
from loom.model.evaluation import EvalRun, EvalSuite
from loom.model.schemas.agent import AgentCreate
from loom.model.schemas.evaluation import EvalRunCreate, EvalSuiteCreate
from loom.model.tenant import Principal, Tenant


def test_eval_run_requires_exactly_one_target(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()

    suite = EvalSuite(**EvalSuiteCreate(
        tenant_id=tenant.id, slug='agent-quality', name='Agent Quality', target_entity_type=VersionedEntityKind.AGENT,
        criteria={'min_accuracy': 0.9}, created_by_id=principal.id,
    ).model_dump())
    session.add(suite)
    session.commit()

    with pytest.raises(sa.exc.IntegrityError):
        session.add(
            EvalRun(
                eval_suite_id=suite.id, target_entity_type=VersionedEntityKind.AGENT,
                status=EvalRunStatus.PENDING, results={}, triggered_by_id=principal.id,
            )
        )
        session.commit()


def test_eval_run_gates_lifecycle_transition(session):
    tenant = Tenant(slug='acme', name='Acme Corp')
    session.add(tenant)
    session.commit()
    principal = Principal(tenant_id=tenant.id, kind='user', display_name='Ada', external_id='ada')
    session.add(principal)
    session.commit()

    agent = Agent(**AgentCreate(
        slug='triage-agent', name='Triage Agent', tenant_id=tenant.id, owner_id=principal.id,
        created_by_id=principal.id, layer=Layer.BUSINESS_OPS, llm_config={}, prompt='p',
        memory_scope=MemoryScope.NONE,
    ).model_dump())
    session.add(agent)
    session.commit()

    suite = EvalSuite(
        tenant_id=tenant.id, slug='agent-quality', name='Agent Quality',
        target_entity_type=VersionedEntityKind.AGENT, criteria={}, created_by_id=principal.id,
    )
    session.add(suite)
    session.commit()

    run = EvalRun(**EvalRunCreate(
        eval_suite_id=suite.id, target_entity_type=VersionedEntityKind.AGENT, target_agent_id=agent.id,
        status=EvalRunStatus.PASSED, results={'accuracy': 0.95}, triggered_by_id=principal.id,
        gates_transition_to=LifecycleState.APPROVED,
    ).model_dump())
    session.add(run)
    session.commit()
    assert run.status == EvalRunStatus.PASSED
    assert run.gates_transition_to == LifecycleState.APPROVED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/model/test_evaluation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.model.evaluation'`

- [ ] **Step 3: Implement**

```python
# src/loom/model/evaluation.py
import datetime
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from loom.model.base import Base, PortableJSON, TimestampMixin, enum_column, exactly_one_of
from loom.model.enums import EvalRunStatus, LifecycleState, VersionedEntityKind


class EvalSuite(Base, TimestampMixin):
    """A named set of regression criteria targeting one versioned entity kind."""

    __tablename__ = 'eval_suite'
    __table_args__ = (sa.UniqueConstraint('tenant_id', 'slug', name='uq_eval_suite_tenant_slug'),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('tenant.id'), index=True)
    slug: Mapped[str] = mapped_column(sa.String(255))
    name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text(), default=None)
    target_entity_type: Mapped[VersionedEntityKind] = mapped_column(
        enum_column(VersionedEntityKind, 'versioned_entity_kind')
    )
    criteria: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    created_by_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'))


class EvalRun(Base, TimestampMixin):
    """One execution of an EvalSuite against a specific versioned entity row; can gate a lifecycle transition."""

    __tablename__ = 'eval_run'
    __table_args__ = (
        sa.CheckConstraint(
            exactly_one_of(
                'target_agent_id',
                'target_skill_id',
                'target_tool_id',
                'target_capability_id',
                'target_datasource_id',
                'target_dataproduct_id',
            ),
            name='ck_eval_run_one_target',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    eval_suite_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('eval_suite.id'), index=True)
    target_entity_type: Mapped[VersionedEntityKind] = mapped_column(
        enum_column(VersionedEntityKind, 'versioned_entity_kind')
    )
    target_agent_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('agent.id'), default=None)
    target_skill_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('skill.id'), default=None)
    target_tool_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey('tool.id'), default=None)
    target_capability_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('capability.id'), default=None
    )
    target_datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('datasource.id'), default=None
    )
    target_dataproduct_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey('dataproduct.id'), default=None
    )
    status: Mapped[EvalRunStatus] = mapped_column(
        enum_column(EvalRunStatus, 'eval_run_status'), default=EvalRunStatus.PENDING
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)
    results: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    triggered_by_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), sa.ForeignKey('principal.id'))
    gates_transition_to: Mapped[LifecycleState | None] = mapped_column(
        enum_column(LifecycleState, 'lifecycle_state'), default=None
    )
```

```python
# src/loom/model/schemas/evaluation.py
import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import EvalRunStatus, LifecycleState, VersionedEntityKind


class EvalSuiteCreate(BaseModel):
    tenant_id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    target_entity_type: VersionedEntityKind
    criteria: dict
    created_by_id: uuid.UUID


class EvalSuiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    target_entity_type: VersionedEntityKind
    criteria: dict
    created_by_id: uuid.UUID


class EvalRunCreate(BaseModel):
    eval_suite_id: uuid.UUID
    target_entity_type: VersionedEntityKind
    target_agent_id: uuid.UUID | None = None
    target_skill_id: uuid.UUID | None = None
    target_tool_id: uuid.UUID | None = None
    target_capability_id: uuid.UUID | None = None
    target_datasource_id: uuid.UUID | None = None
    target_dataproduct_id: uuid.UUID | None = None
    status: EvalRunStatus = EvalRunStatus.PENDING
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    results: dict = {}
    triggered_by_id: uuid.UUID
    gates_transition_to: LifecycleState | None = None


class EvalRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    eval_suite_id: uuid.UUID
    target_entity_type: VersionedEntityKind
    target_agent_id: uuid.UUID | None
    target_skill_id: uuid.UUID | None
    target_tool_id: uuid.UUID | None
    target_capability_id: uuid.UUID | None
    target_datasource_id: uuid.UUID | None
    target_dataproduct_id: uuid.UUID | None
    status: EvalRunStatus
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    results: dict
    triggered_by_id: uuid.UUID
    gates_transition_to: LifecycleState | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/model/test_evaluation.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/model
git commit -m "Add EvalSuite and EvalRun"
```

---

## Task 13: DatabaseConfig, engine.py, RootConfig wiring

**Files:**
- Create: `src/loom/config/database_config.py`
- Modify: `src/loom/config/root_config.py`
- Modify: `src/loom/config/__init__.py`
- Create: `src/loom/model/engine.py`
- Test: `tests/config/test_database_config.py`
- Test: `tests/model/test_engine.py`

**Interfaces:**
- Consumes: `loom.config.base.RootConfigAware` (existing).
- Produces: `loom.config.database_config.DatabaseConfig(host, port, database, username, password)` with computed `dsn: str`; `RootConfig.database: DatabaseConfig`; `loom.model.engine.get_engine(config: DatabaseConfig) -> sa.Engine`, `get_session_factory(config: DatabaseConfig) -> sessionmaker[Session]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_database_config.py
from loom.config.database_config import DatabaseConfig
from loom.config.root_config import RootConfig


def test_default_dsn():
    config = DatabaseConfig()
    assert config.dsn == 'postgresql+psycopg://loom@localhost:5432/loom'


def test_dsn_includes_password_when_set():
    config = DatabaseConfig(password='secret')
    assert config.dsn == 'postgresql+psycopg://loom:secret@localhost:5432/loom'


def test_root_config_has_database_section(tmp_path):
    root = RootConfig(config_path=tmp_path / 'config.yaml')
    assert root.database.database == 'loom'
```

```python
# tests/model/test_engine.py
import sqlalchemy as sa

from loom.config.database_config import DatabaseConfig
from loom.model.engine import get_engine, get_session_factory


def test_get_engine_builds_lazily_without_connecting():
    engine = get_engine(DatabaseConfig(host='unreachable-host'))
    assert isinstance(engine, sa.Engine)
    assert engine.url.drivername == 'postgresql+psycopg'


def test_get_session_factory_is_bound_to_engine():
    factory = get_session_factory(DatabaseConfig())
    assert factory.kw['bind'].url.database == 'loom'
```

Create `tests/config/__init__.py` (empty).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_database_config.py tests/model/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.config.database_config'`

- [ ] **Step 3: Implement**

```python
# src/loom/config/database_config.py
from pydantic import Field, SecretStr, computed_field

from .base import RootConfigAware


class DatabaseConfig(RootConfigAware):
    """Connection settings for the Postgres-backed registry database."""

    host: str = Field(default='localhost', description='Database host')
    port: int = Field(default=5432, description='Database port')
    database: str = Field(default='loom', description='Database name')
    username: str = Field(default='loom', description='Database username')
    password: SecretStr | None = Field(default=None, description='Database password')

    @computed_field
    @property
    def dsn(self) -> str:
        """The SQLAlchemy connection string for this database."""
        auth = self.username
        if self.password is not None:
            auth = f'{self.username}:{self.password.get_secret_value()}'
        return f'postgresql+psycopg://{auth}@{self.host}:{self.port}/{self.database}'
```

Modify `src/loom/config/root_config.py` — add the import and field:

```python
from .database_config import DatabaseConfig
```

```python
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
```

(Insert the field after `config_path` and before the `version` computed field, keeping the existing `from .base import RootConfigAware` import intact.)

Check `src/loom/config/__init__.py` — if it re-exports `RootConfig` by name, add `DatabaseConfig` to the same export list; if it currently just does `from .root_config import RootConfig`, add `from .database_config import DatabaseConfig` alongside it.

```python
# src/loom/model/engine.py
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from loom.config.database_config import DatabaseConfig


def get_engine(config: DatabaseConfig) -> sa.Engine:
    """Build a SQLAlchemy engine from database configuration. Lazy: does not connect until first use."""
    return sa.create_engine(config.dsn)


def get_session_factory(config: DatabaseConfig) -> sessionmaker[Session]:
    """Build a session factory bound to an engine built from database configuration."""
    return sessionmaker(bind=get_engine(config))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/config/test_database_config.py tests/model/test_engine.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/config src/loom/model tests
git commit -m "Add DatabaseConfig, engine helpers, and RootConfig wiring"
```

---

## Task 14: Alembic packaging and initial migration

**Files:**
- Create: `src/loom/migrations/__init__.py`
- Create: `src/loom/migrations/env.py`
- Create: `src/loom/migrations/script.py.mako`
- Create: `src/loom/migrations/versions/__init__.py`
- Create: `src/loom/migrations/versions/0001_initial_schema.py` (generated, see Step 3)
- Create: `alembic.ini` (repo root, dev-only)
- Modify: `pyproject.toml` (package-data)
- Test: `tests/model/test_migrations.py`

**Interfaces:**
- Consumes: `loom.model.base.Base` and every entity module (Tasks 1-12) — all must be imported by `env.py` so their tables register on `Base.metadata`.
- Produces: a runnable Alembic environment reachable via `importlib.resources.files('loom') / 'migrations'`, and one migration (`0001_initial_schema`) that creates every table from Tasks 1-12 and can be downgraded to empty.

- [ ] **Step 1: Write `alembic.ini` (repo root, dev-only convenience)**

```ini
[alembic]
script_location = src/loom/migrations
prepend_sys_path = .
version_path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 2: Write `env.py` and `script.py.mako`**

```python
# src/loom/migrations/env.py
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from loom.model import (  # noqa: F401  (imported for side effect: table registration)
    agent,
    capability,
    dataproduct,
    datasource,
    environment,
    evaluation,
    governance,
    observability,
    skill,
    tenant,
    tool,
)
from loom.model.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option('sqlalchemy.url')
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix='sqlalchemy.', poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

```mako
# src/loom/migrations/script.py.mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create empty `src/loom/migrations/__init__.py` and `src/loom/migrations/versions/__init__.py`.

- [ ] **Step 3: Generate the initial migration**

Run against a throwaway local SQLite file (autogenerate diffs Python metadata against the target DB; the resulting `op.create_table` calls use the portable SQLAlchemy type objects from `Base.metadata`, so generating against SQLite produces a migration that is correct when later run against Postgres):

```bash
rm -f /tmp/loom_autogen_scratch.db
ALEMBIC_URL="sqlite:////tmp/loom_autogen_scratch.db" alembic \
  -x sqlalchemy.url="sqlite:////tmp/loom_autogen_scratch.db" \
  revision --autogenerate -m "Initial registry schema"
```

If `alembic.ini`'s static `sqlalchemy.url` isn't set (it isn't, above), pass it explicitly:

```bash
alembic -c alembic.ini revision --autogenerate -m "Initial registry schema" \
  2>&1 | tee /tmp/alembic_gen.log
```

(If Alembic reports it can't find `sqlalchemy.url`, add a temporary line `sqlalchemy.url = sqlite:////tmp/loom_autogen_scratch.db` under `[alembic]` in `alembic.ini` for this one generation run, then remove it again — the shipped runtime path never reads this file.)

Move/rename the generated file in `src/loom/migrations/versions/` to `0001_initial_schema.py` if Alembic didn't already name it that (the `revision` field inside must stay the auto-generated hash — only the filename changes).

**Verification checklist — inspect `0001_initial_schema.py` for:**
- [ ] `upgrade()` contains one `op.create_table(...)` per entity from Tasks 1-12: `tenant`, `principal`, `environment`, `agent`, `tool`, `datasource`, `dataproduct`, `dataproduct_lineage`, `tool_data_binding`, `skill`, `skill_graph_node`, `skill_graph_edge`, `capability`, `capability_realization`, `metric`, `policy`, `role_binding`, `audit_event`, `eval_suite`, `eval_run` (20 tables).
- [ ] Every `CheckConstraint` from Tasks 7-12 appears (`ck_dataproduct_lineage_one_source`, `ck_tool_data_binding_one_target`, `ck_skill_atomic_content_matches_kind`, `ck_skill_graph_node_one_ref`, `ck_capability_realization_one_realizer`, `ck_metric_one_scope`, `ck_metric_weakest_contributor_at_most_one`, `ck_role_binding_delegation_requires_subset`, `ck_eval_run_one_target`).
- [ ] The six `ix_<table>_current_version` partial unique indexes appear (agent, skill, tool, capability, datasource, dataproduct).
- [ ] `downgrade()` drops every table `upgrade()` created (Alembic autogenerate mirrors this automatically — confirm it isn't `pass`).

- [ ] **Step 4: Update package data so migrations ship in the wheel**

Modify `pyproject.toml`'s `[tool.setuptools.package-data]` — add `"*.mako"` to the existing list:

```toml
[tool.setuptools.package-data]
"*" = ["*.py", "*.yml", "*.yaml", ".j2", "*.md", "inventory", "*.json", ".helmignore", "*.lock", "*.tpl", "*.txt", "*.tcss", "*.mako"]
```

- [ ] **Step 5: Write and run the migration round-trip test**

```python
# tests/model/test_migrations.py
import importlib.resources
import pathlib
import sqlite3

from alembic import command
from alembic.config import Config


def _alembic_config(db_path: pathlib.Path) -> Config:
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{db_path}')
    return config


def test_upgrade_then_downgrade_round_trip(tmp_path):
    db_path = tmp_path / 'loom_migrations_test.db'
    config = _alembic_config(db_path)

    command.upgrade(config, 'head')
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    expected = {
        'tenant', 'principal', 'environment', 'agent', 'tool', 'datasource', 'dataproduct',
        'dataproduct_lineage', 'tool_data_binding', 'skill', 'skill_graph_node', 'skill_graph_edge',
        'capability', 'capability_realization', 'metric', 'policy', 'role_binding', 'audit_event',
        'eval_suite', 'eval_run',
    }
    assert expected <= tables

    command.downgrade(config, 'base')
    conn = sqlite3.connect(db_path)
    remaining = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    } & expected
    conn.close()
    assert remaining == set()
```

Run: `pytest tests/model/test_migrations.py -v`
Expected: PASS (1 passed). If it fails, the failure output identifies exactly which table/constraint the hand-inspected checklist in Step 3 missed — fix `0001_initial_schema.py` directly (do not regenerate; hand-edit) and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/loom/migrations alembic.ini pyproject.toml tests/model/test_migrations.py
git commit -m "Add packaged Alembic migrations and initial registry schema"
```

---

## Task 15: `loom db` CLI subcommand

**Files:**
- Create: `src/loom/cli/db.py`
- Modify: `src/loom/cli/main.py`
- Modify: `README.md`
- Test: `tests/cli/test_db.py`

**Interfaces:**
- Consumes: `loom.config.RootConfig` (existing, extended in Task 13); `alembic.config.Config`, `alembic.command` (Alembic library).
- Produces: `loom.cli.db.db_upgrade/db_downgrade/db_current/db_history/db_revision(config: RootConfig, args: argparse.Namespace) -> int`, matching the existing `config_list`/`config_get`/`config_set` signature convention in `main.py`; a `loom db {upgrade,downgrade,current,history,revision}` CLI surface.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_db.py
import argparse
import pathlib
import sqlite3

import pytest

from loom.cli.db import db_current, db_downgrade, db_history, db_revision, db_upgrade
from loom.config import RootConfig


@pytest.fixture
def sqlite_root_config(tmp_path: pathlib.Path, monkeypatch) -> RootConfig:
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    db_path = tmp_path / 'loom_cli_test.db'
    monkeypatch.setattr(type(config.database), 'dsn', property(lambda self: f'sqlite:///{db_path}'))
    return config


@pytest.mark.asyncio
async def test_db_upgrade_and_downgrade_via_cli_functions(sqlite_root_config, tmp_path):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    db_path = tmp_path / 'loom_cli_test.db'
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert 'tenant' in tables

    await db_downgrade(sqlite_root_config, argparse.Namespace(revision='base'))
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert 'tenant' not in tables


@pytest.mark.asyncio
async def test_db_current_and_history_do_not_raise(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    assert await db_current(sqlite_root_config, argparse.Namespace()) == 0
    assert await db_history(sqlite_root_config, argparse.Namespace()) == 0


@pytest.mark.asyncio
async def test_db_revision_autogenerate_creates_new_file(sqlite_root_config):
    await db_upgrade(sqlite_root_config, argparse.Namespace())
    result = await db_revision(
        sqlite_root_config, argparse.Namespace(message='add scratch table', autogenerate=False)
    )
    assert result == 0
```

Create `tests/cli/__init__.py` (empty).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.cli.db'`

- [ ] **Step 3: Implement**

```python
# src/loom/cli/db.py
import argparse
import importlib.resources

from alembic import command
from alembic.config import Config

from loom.config import RootConfig


def _alembic_config(root_config: RootConfig) -> Config:
    """Build an in-memory Alembic Config pointed at the packaged migrations and the configured DSN."""
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', root_config.database.dsn)
    return config


async def db_upgrade(config: RootConfig, args: argparse.Namespace) -> int:
    command.upgrade(_alembic_config(config), getattr(args, 'revision', None) or 'head')
    return 0


async def db_downgrade(config: RootConfig, args: argparse.Namespace) -> int:
    command.downgrade(_alembic_config(config), args.revision)
    return 0


async def db_current(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    command.current(_alembic_config(config))
    return 0


async def db_history(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    command.history(_alembic_config(config))
    return 0


async def db_revision(config: RootConfig, args: argparse.Namespace) -> int:
    command.revision(_alembic_config(config), message=args.message, autogenerate=args.autogenerate)
    return 0
```

Modify `src/loom/cli/main.py`:

Add the import near the top, alongside the existing `from loom.config import RootConfig`:

```python
from loom.cli.db import db_current, db_downgrade, db_history, db_revision, db_upgrade
```

Add the subparser wiring inside `main()`, immediately after the existing `config_set_parser.set_defaults(func=config_set)` block and before `args = parser.parse_args()`:

```python
        db_parser = subparsers.add_parser('db', help='Database migration commands')
        db_subparser = db_parser.add_subparsers(required=True)

        db_upgrade_parser = db_subparser.add_parser('upgrade', help='Upgrade the database to a revision')
        db_upgrade_parser.add_argument(
            'revision', nargs='?', default='head', help='Target revision, defaults to head'
        )
        db_upgrade_parser.set_defaults(func=db_upgrade)

        db_downgrade_parser = db_subparser.add_parser('downgrade', help='Downgrade the database to a revision')
        db_downgrade_parser.add_argument('revision', help='Target revision')
        db_downgrade_parser.set_defaults(func=db_downgrade)

        db_current_parser = db_subparser.add_parser('current', help='Show the current database revision')
        db_current_parser.set_defaults(func=db_current)

        db_history_parser = db_subparser.add_parser('history', help='Show migration history')
        db_history_parser.set_defaults(func=db_history)

        db_revision_parser = db_subparser.add_parser('revision', help='Create a new migration revision')
        db_revision_parser.add_argument('-m', '--message', required=True, help='Revision message')
        db_revision_parser.add_argument(
            '--autogenerate', action='store_true', default=False, help='Autogenerate from model changes'
        )
        db_revision_parser.set_defaults(func=db_revision)
```

Modify `README.md` — append a short section:

```markdown
## Database

Loom stores its registry (Capabilities, Agents, Skills, Tools, DataSources,
DataProducts, and governance/eval records) in PostgreSQL. Configure the
connection once:

    loom config set database.host db.example.com
    loom config set database.username loom
    loom config set database.password <secret>

Then apply migrations — no separate `alembic` install or invocation needed:

    loom db upgrade

Other database commands: `loom db downgrade <revision>`, `loom db current`,
`loom db history`, `loom db revision -m "message" [--autogenerate]`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cli/test_db.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests across Tasks 1-15 PASS.

Run: `ruff format src/loom && ruff check src/loom`
Expected: no changes needed / no lint errors (fix any that appear before committing).

Run: `pyrefly check`
Expected: no type errors (fix any that appear before committing; if a type error stems from a SQLAlchemy `Mapped[...]` pattern pyrefly doesn't understand well, add a narrowly-scoped `# type: ignore` on that exact line only, never a blanket suppression).

- [ ] **Step 6: Commit**

```bash
git add src/loom/cli README.md tests/cli
git commit -m "Add loom db CLI subcommand for end-user-driven migrations"
```

---

## Self-Review Notes

- **Spec coverage:** every entity in the design doc's Entities section maps to exactly one task (Tenant/Principal → 1, VersionedEntityMixin → 2, Environment → 3, Agent → 4, Tool → 5, DataSource → 6, DataProduct/Lineage/ToolDataBinding → 7, Skill/Graph → 8, Capability/Realization → 9, Metric → 10, Policy/RoleBinding/AuditEvent → 11, EvalSuite/EvalRun → 12); CLI-driven Alembic section → Tasks 13-15.
- **Placeholder scan:** no TBD/TODO; every step has runnable code and an exact command. The one deliberately-approximate step is Task 14 Step 3 (autogenerate output can't be hand-typed blind) — it's backed by an explicit inspection checklist plus a passing round-trip test that will fail loudly and specifically if the checklist was insufficient.
- **Type consistency:** verified `exactly_one_of`, `enum_column`, `current_version_index`, `PortableJSON`, `VersionedEntityMixin`/`VersionedEntityCreate`/`VersionedEntityRead` are imported with identical names and signatures everywhere they're consumed (Tasks 3-13 all import from `loom.model.base`/`loom.model.schemas.base` without renaming).
- **Empirically verified before writing this plan** (via scratch scripts, not part of any task): mixin-declared `ForeignKey` columns copy correctly per subclass in SQLAlchemy 2.0; the `postgresql_where`/`sqlite_where` partial-unique-index pattern enforces "exactly one current version" on SQLite; `enum_column`'s `values_callable` persists the lowercase `.value` (not the uppercase member name); the `exactly_one_of` CHECK constraint correctly rejects both the zero-set and two-set cases on SQLite.
