# Registry physical data model (PostgreSQL) — design

Date: 2026-08-07
Status: Approved by user, pending implementation

## Overview

Physical data model for the Catalog + Governance + Data planes described in
the top-level `CLAUDE.md` (the "Registry"), targeting PostgreSQL. Rendered as:

- SQLAlchemy 2.0 declarative ORM classes (source of truth for the schema)
- Pydantic v2 wire schemas (Create/Update/Read) for the REST API, mirroring
  the ORM 1:1
- An Alembic migration, packaged and driven by the `loom` CLI so end-users
  never touch `alembic` directly

Full domain model in one pass — no phased subset.

## Scope

**In scope**: `Capability`, `Agent`, `Skill` (+ graph nodes/edges),
`Tool` (+ data bindings), `DataSource`, `DataProduct` (+ lineage),
`CapabilityRealization`, `Policy`, `RoleBinding`, `AuditEvent`, `EvalSuite`,
`EvalRun`, `Environment`, plus the *derived* `Metric` rollup row
(`realization_score`).

**Out of scope**:
- Raw `Trace`/`TraceStep`/time-series `Metric` points. Per the architecture
  section of `CLAUDE.md`, the Trace Store and Metrics backend are physically
  separate, event-bus-decoupled stores from the Registry. Only the rollup
  `Metric` (produced by the Capability Rollup Processor) is persisted here.
- Business/service logic: the Policy Engine's runtime authorization
  decisions, the Skill Graph Validator's compiler logic, and lifecycle
  transition gating logic are not implemented here. This spec defines the
  *shape* that logic operates over (e.g. `EvalRun.status`, `RoleBinding`),
  not the logic itself.
- Credential vault contents: `Tool.auth_binding_id` /
  `DataSource.connection_binding_id` are opaque pointers into an external
  vault, not modeled here.

## Two additions not literally named in `CLAUDE.md`'s entity table

Flagged per the doc's own instruction to flag before assuming:

- **`Principal`** — RBAC subjects and `owner_id`/`created_by_id`/
  `approved_by_id` need a referenceable identity. Without it those columns
  are unenforced free text. Kinds: `user`, `agent`, `service_account`.
- **`Tenant`** — `CLAUDE.md` repeatedly references tenant isolation /
  tenant-partitioned stores / Org-scoped memory but names no entity for it.
  `Environment` and `Principal` are scoped to `Tenant`; other entities are
  scoped transitively through those.

## Package layout

```
src/loom/model/
  __init__.py
  base.py              # DeclarativeBase, TimestampMixin, VersionedEntityMixin
  enums.py
  tenant.py             # Tenant, Principal
  environment.py         # Environment
  capability.py          # Capability, CapabilityRealization
  agent.py               # Agent
  skill.py               # Skill, SkillGraphNode, SkillGraphEdge
  tool.py                # Tool, ToolDataBinding
  datasource.py           # DataSource
  dataproduct.py          # DataProduct, DataProductLineage
  observability.py        # Metric (rollup only)
  governance.py           # Policy, RoleBinding, AuditEvent
  eval.py                 # EvalSuite, EvalRun
  engine.py               # get_engine()/get_session_factory() from DatabaseConfig
  schemas/                # Pydantic Create/Update/Read wire models, 1:1 with ORM
    __init__.py
    tenant.py
    environment.py
    capability.py
    agent.py
    skill.py
    tool.py
    datasource.py
    dataproduct.py
    observability.py
    governance.py
    eval.py

src/loom/migrations/      # packaged with the wheel, driven via importlib.resources
  env.py
  script.py.mako
  versions/
    0001_initial_schema.py

src/loom/cli/db.py        # loom db {upgrade,downgrade,current,history,revision}
src/loom/config/database_config.py

alembic.ini                # repo root, dev-only convenience (autogenerate)
```

## Shared mixins & enums (`base.py`, `enums.py`)

- `TimestampMixin`: `created_at`, `updated_at` (server-defaulted).
- `VersionedEntityMixin` (used by `Capability`, `Agent`, `Skill`, `Tool`,
  `DataSource`, `DataProduct`): `id` (UUID PK, one row per version),
  `entity_id` (UUID, stable across versions), `version` (int),
  `is_current` (bool — enforced unique via a partial unique index
  `WHERE is_current`), `slug`, `name`, `description`, `lifecycle_state`,
  `maturity`, `owner_id` (FK `principal`), `classification`,
  `created_by_id` (FK `principal`), `created_at`, `approved_by_id`
  (FK `principal`, nullable), `approved_at` (nullable), `tenant_id`
  (FK `tenant`). Unique constraint `(entity_id, version)`.

  **Versioning is row-per-version, immutable.** A lifecycle transition or
  content change always inserts a new row; there is no in-place `UPDATE` of
  `lifecycle_state` on an existing row. This is enforced by not exposing a
  setter in the service layer (out of scope here) — the schema makes the
  "current" pointer a first-class, indexed fact instead of implicit
  "latest by timestamp".

- Enums: `LifecycleState` (draft/in_review/approved/published/deprecated/
  retired), `MaturityLevel` (experimental/beta/stable/deprecated),
  `Classification` (public/internal/confidential/restricted), `Layer`
  (infra_ops/business_tech/business_ops — applies to `Agent` and `Skill`
  only; `Tool` is a layer-less leaf), `MemoryScope`
  (session/user/org/none), `EnvironmentKind`
  (sandbox/staging/production), `DataSourceKind`
  (database/api/vector_store/stream), `SkillKind` (atomic/composite),
  `GraphNodeType` (agent/skill/tool), `DataBindingAccessMode`
  (read/write/read_write), `PrincipalKind`
  (user/agent/service_account), `PolicyEffect` (allow/deny),
  `PolicyScopeType` (entity/invocation/data_scope/environment),
  `EvalRunStatus` (pending/running/passed/failed), `RealizingEntityType`
  (agent/skill/tool), `AuditDecision` (allow/deny).

## Entities

### `Tenant` / `Principal` (`tenant.py`)
- `Tenant`: `id`, `slug` (unique), `name`, `created_at`.
- `Principal`: `id`, `tenant_id` FK, `kind`, `display_name`,
  `external_id` (unique per tenant), `created_at`.

### `Environment` (`environment.py`)
`id`, `tenant_id` FK, `name`, `kind`, `compute_boundary_ref`,
`network_boundary_ref`, `created_at`. Unique `(tenant_id, name)`. Physical
isolation itself (separate compute/network) is infra, not represented
beyond these reference strings.

### `Capability` (`capability.py`)
`VersionedEntityMixin` + `target_metrics` (JSONB list of
`{metric_name, target_value, unit, comparator}`).

`CapabilityRealization`: `id`, `capability_id` FK `capability.id`,
`realizing_entity_type`, plus three **mutually exclusive** nullable FKs
(`realizing_agent_id`, `realizing_skill_id`, `realizing_tool_id`) each
pointing at a specific version row, `contribution_weight` (numeric),
`created_at`. CHECK constraint: exactly one of the three FKs is set and
matches `realizing_entity_type`.

### `Agent` (`agent.py`)
`VersionedEntityMixin` + `layer`, `model_config` (JSONB), `prompt` (text —
prompt versioning *is* Agent versioning), `memory_scope`,
`permission_boundary` (JSONB, design-time declared boundary; runtime
enforcement is the Policy Engine's job, out of scope).

### `Skill` (`skill.py`)
`VersionedEntityMixin` + `layer`, `kind`, `is_entry_point` (bool),
`atomic_content` (JSONB, nullable). CHECK: `kind=atomic` ⇒
`atomic_content IS NOT NULL`; `kind=composite` ⇒ `atomic_content IS NULL`.

`SkillGraphNode`: `id`, `skill_id` FK `skill.id` (owning composite version),
`node_key`, `node_type`, three mutually-exclusive nullable FKs
(`agent_id`, `skill_ref_id`, `tool_id`), `position` (JSONB, nullable).
Unique `(skill_id, node_key)`. CHECK: exactly one FK set, matching
`node_type`.

`SkillGraphEdge`: `id`, `skill_id` FK (owning composite version),
`from_node_id` / `to_node_id` FK `skill_graph_node.id`, `created_at`.
Unique `(skill_id, from_node_id, to_node_id)`. Normalized so the compiler's
layer-descent and cycle-detection checks are plain SQL over real FKs.

### `Tool` (`tool.py`)
`VersionedEntityMixin` + `invocation_spec` (JSONB), `auth_binding_id`
(UUID, opaque vault pointer).

`ToolDataBinding`: `id`, `tool_id` FK `tool.id`, mutually-exclusive
nullable FKs `datasource_id` / `dataproduct_id` (both point at a specific
*version* row — **version-pinned**, per the doc's "leaning pinned" note),
`access_mode`, `created_at`. CHECK: exactly one of the two FKs set.

### `DataSource` (`datasource.py`)
`VersionedEntityMixin` + `kind`, `connection_binding_id` (UUID, opaque
vault pointer).

### `DataProduct` (`dataproduct.py`)
`VersionedEntityMixin` + `contract` (JSONB schema contract).

`DataProductLineage`: `id`, `dataproduct_id` FK (the product being
described), mutually-exclusive nullable FKs `source_datasource_id` /
`source_dataproduct_id` (self-referential, for chained products). CHECK:
exactly one source set. Classification propagation along lineage edges is
service-layer logic, out of scope here.

### `Metric` (`observability.py`) — rollup only
`id`, `tenant_id` FK, mutually-exclusive nullable FKs `capability_id` /
`agent_id` / `skill_id` / `tool_id` (a metric scopes to exactly one
entity, or rolls up to a Capability), `metric_name`, `value` (numeric),
`unit` (nullable), `is_realization_score` (bool), mutually-exclusive
nullable FKs `weakest_contributor_agent_id` /
`weakest_contributor_skill_id` / `weakest_contributor_tool_id` (surfaces
the single weakest contributor per principle 3), `otel_resource_ref`
(str — opaque pointer into the real Trace/Metrics store), `computed_at`.

### Governance (`governance.py`)
- `Policy`: `id`, `tenant_id` FK, `name`, `description`, `effect`,
  `scope_type`, `rule` (JSONB), `created_by_id` FK, `created_at`.
- `RoleBinding`: `id`, `tenant_id` FK, `principal_id` FK (subject), `role`,
  `scope_type`, `scope_ref` (JSONB), `environment_id` FK nullable,
  `delegated_from_principal_id` FK `principal` nullable,
  `permission_subset` (JSONB, nullable — required whenever
  `delegated_from_principal_id` is set, modeling "constrained, not
  full-permission" delegation), `created_by_id` FK, `created_at`.
- `AuditEvent`: `id`, `tenant_id` FK, `occurred_at`, `actor_principal_id`
  FK, `acting_as_principal_id` FK nullable (impersonation), `action`,
  `entity_type`, `entity_id` (nullable), `environment_id` FK nullable,
  `decision`, `policy_id` FK nullable, `details` (JSONB). Append-only by
  convention — no `Update`/`Delete` Pydantic schema is generated for it,
  and a follow-up migration can `REVOKE UPDATE, DELETE` at the DB role
  level (noted, not implemented in the initial migration).

### Eval (`eval.py`)
- `EvalSuite`: `id`, `tenant_id` FK, `slug`, `name`, `description`,
  `target_entity_type`, `criteria` (JSONB), `created_by_id` FK,
  `created_at`.
- `EvalRun`: `id`, `eval_suite_id` FK, `target_entity_type`, six
  mutually-exclusive nullable FKs (one per versioned entity type:
  `target_agent_id`, `target_skill_id`, `target_tool_id`,
  `target_capability_id`, `target_datasource_id`,
  `target_dataproduct_id`), `status`, `started_at`, `completed_at`
  (nullable), `results` (JSONB), `triggered_by_id` FK,
  `gates_transition_to` (`LifecycleState`, nullable — which transition a
  passing run authorizes).

## Pydantic wire schemas (`model/schemas/`)

Per entity: `XCreate` (client input, no server-generated fields),
`XRead` (`model_config = ConfigDict(from_attributes=True)`, full row incl.
`id`/`created_at`/etc.), and `XUpdate` (partial, only for entities that are
genuinely mutable in place — `Tenant`, `Principal`, `Environment`,
`Policy`, `RoleBinding`. Versioned entities have no `Update` schema; a
"change" is a new `XCreate` against the same `entity_id`, producing a new
version row).

## CLI-driven Alembic (config + `loom db`)

- `src/loom/config/database_config.py`: `DatabaseConfig(RootConfigAware)`
  — `host`, `port`, `database`, `username`, `password`, plus a
  `computed_field` `dsn` → `postgresql+psycopg://...`, following the same
  pattern as `RootConfig.version`. `RootConfig` gains
  `database: DatabaseConfig`.
- Migrations live inside the package at `src/loom/migrations/` (not repo
  root) so they ship in the wheel and are reachable via
  `importlib.resources.files('loom') / 'migrations'` regardless of install
  location.
- `src/loom/cli/db.py` builds an `alembic.config.Config()` object in
  memory (`script_location` from the packaged path, `sqlalchemy.url` from
  `RootConfig.database.dsn`) and drives it via `alembic.command`. New
  `loom db` subcommand group: `upgrade [rev=head]`, `downgrade <rev>`,
  `current`, `history`, `revision -m "..." [--autogenerate]`. No
  standalone `alembic` CLI use required by end-users.
- A thin repo-root `alembic.ini` remains for local dev convenience only
  (`alembic revision --autogenerate` during development); the shipped CLI
  path never reads it.
- New dependencies: `sqlalchemy>=2.0`, `alembic>=1.14`,
  `psycopg[binary]>=3.2`.

## IDs

UUID primary keys throughout, server-defaulted via Postgres'
`gen_random_uuid()` (built in since PG13, no extension required).

## Open items carried from `CLAUDE.md` (not resolved here)

- `Tool.data_bindings[]` pinned vs. floating — resolved **pinned** for
  this schema, per the doc's own leaning.
- `CapabilityRealization.contribution_weight` hand-authored vs.
  empirically derived — schema stores a single numeric value either way;
  which process populates it is out of scope.
- Client-side vs. server-side duplication of Skill Graph Validator logic —
  irrelevant to the physical model; the server-side check has real data to
  query regardless.
