# Catalog REST API — design

Date: 2026-08-08
Status: Approved by user, pending implementation

## Overview

A standalone FastAPI component fronting the Catalog slice of the registry
physical data model built previously (`src/loom/model/`), aligned with
pragmatic Domain-Driven Design. This is one of several planned components —
Governance and Eval will become their own components later, for
segregation of concerns and independent scalability. This component owns
only the Catalog aggregates.

A follow-on effort will expose the same use-cases as MCP tools via
`fastmcp`; the Service layer defined here is written so it can be reused
by that effort without duplicating business logic (Router and MCP-tool
adapter both call the same Services).

## Scope

**In scope — Catalog aggregates**: `Capability` (+`CapabilityRealization`),
`Agent`, `Skill` (+`SkillGraphNode`/`SkillGraphEdge`), `Tool`
(+`ToolDataBinding`), `DataSource`, `DataProduct` (+`DataProductLineage`).

**Out of scope**:
- `Tenant`/`Principal`/`Environment` lifecycle endpoints — owned by a
  future platform/identity component. This component has **read-only**
  access to `principal` (to resolve an authenticated caller to a
  `principal_id`) and otherwise treats `tenant_id`/`owner_id` as foreign
  UUIDs, relying on the existing DB-level FK constraints.
- `Policy`/`RoleBinding`/`AuditEvent`/`EvalSuite`/`EvalRun` — future
  Governance and Eval components.
- Eval-gating of lifecycle transitions and RoleBinding/Policy evaluation.
  Transitions are validated against a structural state machine only (see
  below) — "requires a passing EvalRun" is explicitly deferred to the
  future Eval component, consistent with the physical-model spec's own
  boundary.
- `DELETE` endpoints. The physical schema is append-only/immutable by
  design (row-per-version, no delete semantics were built); nothing in
  this component deletes rows.

## Architecture — pragmatic DDD

`Router → Service (use-cases) → Repository → ORM`, one module per
aggregate, grouped under `src/loom/api/catalog/`. `loom.model`'s ORM
classes remain the domain model — their invariants already live in CHECK
constraints and the `VersionedEntityMixin`/`exactly_one_of` machinery, so
there is no separate hand-rolled domain-entity layer or ORM↔domain mapping
step. `loom.model.schemas.*` Pydantic classes are reused as the DTOs
wherever they fit; a small number of API-specific request schemas exist
where the wire shape must differ from the persistence-facing shape (see
Auth below — `tenant_id`/`created_by_id` are never client-supplied).

- **Router**: HTTP adapter only — path/query parsing, calls exactly one
  Service method, no business logic.
- **Service**: use-cases (`create_capability`, `create_new_version`,
  `transition_lifecycle`, `add_realization`, ...). Owns version-bump
  semantics, the lifecycle state machine, and translates
  `sqlalchemy.exc.IntegrityError` into typed domain exceptions. HTTP-agnostic.
- **Repository**: persistence abstraction over `AsyncSession` queries
  against `loom.model`. Every method takes `tenant_id` and filters by it —
  tenant isolation is a hard default here, not an opt-in per-call check.

## Async

New `get_async_engine(config: DatabaseConfig) -> AsyncEngine` /
`get_async_session_factory(config) -> async_sessionmaker[AsyncSession]` in
`src/loom/model/engine.py`, alongside the existing sync helpers (used by
the CLI/Alembic, unchanged). `psycopg` v3 supports both sync and async
under the same `postgresql+psycopg://` URL — `create_async_engine` picks
the async path automatically, no new driver dependency.

## Package layout

```
src/loom/api/
  __init__.py
  catalog/
    __init__.py
    main.py               # FastAPI() app, lifespan (engine setup/dispose),
                           # include_router calls, exception handlers, run()
                           # entry point for the `loom-catalog-api` script
    dependencies.py        # get_session, get_current_token, get_current_principal,
                           # require_scopes(*scopes) dependency factory
    security.py            # JWT validation (PyJWKClient-backed), AuthenticatedPrincipal
    exceptions.py           # EntityNotFoundError, VersionConflictError, DomainValidationError
                           # + FastAPI exception handlers mapping them to HTTP responses
    pagination.py            # Page[T] response envelope + limit/offset query dependency
    capability/
      __init__.py
      router.py
      service.py
      repository.py
      schemas.py            # API-level request schemas (omit tenant_id/created_by_id)
    agent/
      router.py / service.py / repository.py / schemas.py
    skill/
      router.py / service.py / repository.py / schemas.py
      # graph node/edge sub-resource endpoints live in the same router
    tool/
      router.py / service.py / repository.py / schemas.py
      # data-binding sub-resource endpoints live in the same router
    datasource/
      router.py / service.py / repository.py / schemas.py
    dataproduct/
      router.py / service.py / repository.py / schemas.py
      # lineage sub-resource endpoints live in the same router

src/loom/config/auth_config.py   # AuthConfig, wired into RootConfig
```

Runnable via `uvicorn loom.api.catalog.main:app`, or the new
`loom-catalog-api` console script (`main.py`'s `run()` calls
`uvicorn.run(...)`), registered alongside the existing `loom` script in
`pyproject.toml`.

## Authentication & Authorization

**Mechanism**: OAuth2/OIDC Bearer JWT, IDP-agnostic. New `AuthConfig`
(`src/loom/config/auth_config.py`, same `RootConfigAware` pattern as
`DatabaseConfig`): `issuer: str`, `audience: str`, `jwks_uri: str | None`
(derived from `{issuer}/.well-known/openid-configuration` if unset),
`algorithms: list[str] = ['RS256']`. Wired into `RootConfig.auth`.

Validated with `PyJWT` + `PyJWKClient` (signature, `exp`, `iss`, `aud`) —
no vendor SDK. `get_current_token` dependency decodes and returns claims;
`get_current_principal` resolves them into an `AuthenticatedPrincipal`
value object (`principal_id: uuid.UUID`, `tenant_id: uuid.UUID`,
`scopes: set[str]`).

**Principal resolution, not client-supplied identity.** The token's `sub`
claim is an external identity string, not our internal `principal.id`
UUID. `get_current_principal` does a **read-only** query against the
shared `principal` table (`Principal.external_id == sub AND
Principal.tenant_id == <tenant claim>`) to resolve the UUID — 401 if no
match. Consequence for every write endpoint: request bodies **never**
accept `tenant_id` or `created_by_id`; those are always the resolved
`AuthenticatedPrincipal`'s values, never trusted from the client.
`owner_id` may still be client-specified (defaults to the caller) since
assigning ownership to someone else is a legitimate action distinct from
"who actually performed this write."

**Scopes** — `catalog:{resource}:{action}`, three actions per aggregate:

```
catalog:capability:{read,write,transition}
catalog:agent:{read,write,transition}
catalog:skill:{read,write,transition}
catalog:tool:{read,write,transition}
catalog:datasource:{read,write,transition}
catalog:dataproduct:{read,write,transition}
```

18 scopes. `write` covers create-entity and create-new-version.
`transition` covers lifecycle-state changes — kept separate from `write`
since approval is often a distinct responsibility from authoring
(separation of duties). Sub-resources (graph nodes/edges, data bindings,
lineage, realizations) inherit their owning aggregate's `write` scope
rather than getting their own scope, to avoid scope explosion.

**Roles** (IDP-side bundles; read from a space-separated `scope` claim per
OAuth2 convention, or a `roles` claim expanded via a static server-side
role→scope table — support both):

| Role | Scopes |
|---|---|
| `catalog-viewer` | all `:read` |
| `catalog-editor` | all `:read` + `:write` |
| `catalog-approver` | all `:read` + `:transition` |
| `catalog-admin` | everything |

**Enforcement**: `require_scopes(*scopes)` dependency factory applied
per-route. Missing/invalid token → 401. Valid token, insufficient scope →
403.

## Versioning semantics on the API

For each versioned aggregate (Capability, Agent, Skill, Tool, DataSource,
DataProduct):

- `POST /{resource}` — new entity, version 1, `lifecycle_state = draft`.
- `GET /{resource}` — list current versions, paginated, tenant-scoped.
  Filters: `lifecycle_state`, `slug` (exact match). Requires `:read`.
- `GET /{resource}/{entity_id}` — current version. Requires `:read`.
- `GET /{resource}/{entity_id}/versions` — list all versions. Requires `:read`.
- `GET /{resource}/{entity_id}/versions/{version}` — specific version. Requires `:read`.
- `POST /{resource}/{entity_id}/versions` — new version: Service loads the
  current row, flips its `is_current` to `False`, inserts a new row at
  `version + 1` with `is_current = True` and `lifecycle_state = draft`
  (a new version always restarts the governance workflow). Requires `:write`.
- `POST /{resource}/{entity_id}/versions/{version}/transitions` — body
  `{"to_state": "<LifecycleState>"}`. Validated against a fixed structural
  state machine (below); **mutates `lifecycle_state` in place on the
  current version row** (see the flagged deviation at the top of this
  doc), setting `approved_by_id`/`approved_at` to the caller when
  transitioning into `approved`. Requires `:transition`. 409 if the target
  state isn't a legal transition from the current state, or if
  `{version}` isn't the entity's current version.

**Structural lifecycle state machine** (no eval/policy gating — that's
the future Eval/Governance components' job):

```
draft → in_review
in_review → approved | draft   (draft = rejected back to author)
approved → published
published → deprecated
deprecated → retired
```

**Sub-resources** (plain `TimestampMixin` join/graph tables, not
themselves versioned — create + list only, no update/delete, tied to a
specific owning version row via FK):

- `POST /capabilities/{entity_id}/versions/{version}/realizations`, `GET .../realizations`
- `POST /skills/{entity_id}/versions/{version}/nodes`, `GET .../nodes`
- `POST /skills/{entity_id}/versions/{version}/edges`, `GET .../edges`
- `POST /tools/{entity_id}/versions/{version}/data-bindings`, `GET .../data-bindings`
- `POST /dataproducts/{entity_id}/versions/{version}/lineage`, `GET .../lineage`

All routes are mounted under an `/api/v1` prefix.

## Error handling

Domain exceptions (`src/loom/api/catalog/exceptions.py`), HTTP-agnostic,
raised by Services:

- `EntityNotFoundError` → 404
- `VersionNotFoundError` → 404
- `IllegalTransitionError` (not a legal state-machine edge, or target
  version isn't current) → 409
- `DomainValidationError` (wraps a translated `IntegrityError` — e.g. a
  CHECK constraint violation from `exactly_one_of`) → 422

Registered via FastAPI exception handlers in `main.py`, producing a
consistent envelope: `{"error_code": "...", "message": "..."}`.

## Pagination

`Page[T]` response envelope (`items: list[T]`, `total: int`, `limit: int`,
`offset: int`) + a shared `PaginationParams` query-parameter dependency
(`limit` default 50 / max 200, `offset` default 0). Simple limit/offset —
cursor pagination is unnecessary complexity (YAGNI) for this pass.

## Testing

`httpx.AsyncClient` with `ASGITransport` against the FastAPI app.
`get_session` and `get_current_principal` are dependency-overridden per
test: the former to inject a test DB session (SQLite in-memory, same
pattern as `tests/conftest.py`, extended with an async engine/session
fixture), the latter to inject a fake `AuthenticatedPrincipal` without
real JWT validation. `security.py`'s JWT-decoding logic itself gets a
narrow, separate unit test with a stubbed JWKS response — the only place
real token validation is exercised.

## New dependencies

- `fastapi>=0.115` — core
- `uvicorn[standard]>=0.32` — core (also the component's runtime server)
- `pyjwt[crypto]>=2.9` — core
- `httpx>=0.27` — dev (test client)

## Out of scope / open items carried forward

- The lifecycle-state-mutates-in-place deviation from the physical-model
  spec's original "no in-place UPDATE" wording (flagged above) —
  reconsider if a future audit requirement wants every governance-state
  change to also be a distinct content version.
- Cross-tenant admin/superuser access (e.g. a support role that can see
  all tenants) is not modeled; every request is scoped to exactly one
  tenant from the token.
- Rate limiting, request-size limits, and other API-gateway-shaped
  concerns are assumed to be handled by infrastructure in front of this
  component (matches `CLAUDE.md`'s "gateway enforces the same RBAC/Policy
  decisions physically" framing), not implemented here.
