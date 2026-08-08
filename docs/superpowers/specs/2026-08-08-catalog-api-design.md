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

**In scope — platform bootstrap**: `Tenant`, `Principal`, `Environment` —
plain CRUD (no `DELETE`, consistent with the rest of this API), gated
behind a distinct, higher-privilege scope tier (see Authentication &
Authorization) rather than deferred to a future component. This is the
mechanism by which a platform admin provisions a Tenant, its
Environment(s), and the Principals who are then granted entitlements (via
IDP-side role assignment) to work with catalog content.

**Out of scope**:
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
    tenant/
      router.py / service.py / repository.py     # platform-tier, plain CRUD
    principal/
      router.py / service.py / repository.py     # platform-tier, plain CRUD
    environment/
      router.py / service.py / repository.py     # platform-tier, plain CRUD

src/loom/config/auth_config.py   # AuthConfig, wired into RootConfig

src/loom/idp/                      # IDP-agnostic admin-operations abstraction
  __init__.py
  client.py                        # IdpAdminClient protocol, ClientRegistrationResult, RoleDefinition
  keycloak.py                       # KeycloakAdminClient(IdpAdminClient)
  catalog_roles.py                   # the 24 scopes + 5 composite roles as data (single source of truth)

src/loom/cli/idp.py                # loom idp register-client
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

`principal_id` is deliberately **not** set equal to the `sub` claim
itself — `sub` is only unique within its issuer, isn't guaranteed to be a
UUID, and rotates if the org ever migrates/federates IDPs, which would
otherwise force rewriting every historical `created_by_id`/`approved_by_id`
FK across the registry. Keeping our own stable internal UUID (with
`Principal.external_id` holding the current `sub`, already uniquely
constrained per-tenant by `uq_principal_tenant_external_id`) also makes
the lookup itself a real authorization gate: a valid, correctly-scoped
token from the trusted issuer is not sufficient to act as a principal
unless a `Principal` row was deliberately provisioned for that identity.
`AuthConfig` assumes a single trusted issuer for this pass; a future
multi-IDP-per-tenant setup would need the natural key widened to
`(tenant_id, issuer, external_id)` to rule out cross-issuer `sub`
collisions — noted as an explicit out-of-scope assumption, not solved here.

**Platform-tier auth dependency split.** `Tenant`/`Principal`/`Environment`
carry no `owner_id`/`created_by_id` FK, so their endpoints depend on
`get_current_token` + `require_scopes(...)` only — no principal resolution.
This is a deliberate design choice, not an oversight: `get_current_principal`
401s when no matching `Principal` row exists yet, which would otherwise
deadlock the very first admin on a fresh deployment. Bootstrapping proceeds
top-down: a platform-admin token creates the `Tenant`, its `Environment`(s),
and the `Principal` row(s) for the humans/agents who'll actually author
catalog content — only once those `Principal` rows exist do *their* tokens
resolve, letting them use content-tier (`catalog:{resource}:*`) endpoints.

**Scopes** — `catalog:{resource}:{action}`, three actions per aggregate:

```
catalog:capability:{read,write,transition}
catalog:agent:{read,write,transition}
catalog:skill:{read,write,transition}
catalog:tool:{read,write,transition}
catalog:datasource:{read,write,transition}
catalog:dataproduct:{read,write,transition}
```

18 content scopes. `write` covers create-entity and create-new-version.
`transition` covers lifecycle-state changes — kept separate from `write`
since approval is often a distinct responsibility from authoring
(separation of duties). Sub-resources (graph nodes/edges, data bindings,
lineage, realizations) inherit their owning aggregate's `write` scope
rather than getting their own scope, to avoid scope explosion.

**Platform scopes** — same `catalog:{resource}:{action}` shape, no
`transition` action (these aren't versioned):

```
catalog:tenant:{read,write}
catalog:principal:{read,write}
catalog:environment:{read,write}
```

6 more scopes, 24 total. `catalog:principal:write`/`catalog:environment:write`
accept a client-specified `tenant_id` in the request body — the one place
in this API where `tenant_id` is trusted from the client rather than
derived from the token, since a platform admin may provision across
tenants. This makes issuing those two scopes a meaningfully more sensitive
IDP-side decision than any content scope. `catalog:tenant:*` has no
per-request tenant to scope against at all (`Tenant` has no `tenant_id`
column — it *is* the tenant); the scope itself is the authorization.

**Roles** (IDP-side bundles; read from a space-separated `scope` claim per
OAuth2 convention, or a `roles` claim expanded via a static server-side
role→scope table — support both):

| Role | Scopes |
|---|---|
| `catalog-viewer` | all content `:read` |
| `catalog-editor` | all content `:read` + `:write` |
| `catalog-approver` | all content `:read` + `:transition` |
| `catalog-admin` | all content scopes (read+write+transition) |
| `catalog-platform-admin` | `catalog-admin` + all platform scopes |

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

## Platform bootstrap endpoints

Plain CRUD, not versioned (`Tenant`/`Principal`/`Environment` already have
`Update` schemas from the physical-model work); no `DELETE`, consistent
with the rest of this API. Auth per the platform-tier dependency split
above (`get_current_token` + `require_scopes(...)`, no principal
resolution):

- `POST /tenants`, `GET /tenants`, `GET /tenants/{id}`, `PATCH /tenants/{id}`
  — requires `catalog:tenant:{read,write}`.
- `POST /principals`, `GET /principals`, `GET /principals/{id}`, `PATCH /principals/{id}`
  — requires `catalog:principal:{read,write}`; `tenant_id` is
  client-specified in the body (see Authentication & Authorization).
- `POST /environments`, `GET /environments`, `GET /environments/{id}`, `PATCH /environments/{id}`
  — requires `catalog:environment:{read,write}`; `tenant_id`
  client-specified likewise.

All routes (content and platform) are mounted under an `/api/v1` prefix.

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

## IDP client & role bootstrap (`loom idp register-client`)

A single trusted IDP for this pass, assumed to be Keycloak — but the
integration is written behind a provider-agnostic interface so a
different IDP can be substituted later without touching the CLI command
or the role-vocabulary definitions.

**Caveat, stated plainly rather than glossed over**: Keycloak's "Initial
Access Token" (generated under Client Registration settings) is scoped
narrowly to Dynamic Client Registration (RFC 7591) — creating the OAuth
client itself. It does not inherently carry realm-role-management rights.
Declaring the role vocabulary needs the Admin REST API, which needs a
realm-management-scoped token. In practice the token supplied to this
command must be capable of both — typically an access token from a
realm-admin service account via `client_credentials`, not strictly the
narrow DCR-only token — and the CLI's `--help` text and docs say so
explicitly rather than assuming a bare Initial Access Token suffices.

**Provider abstraction** (`src/loom/idp/`):

```python
class IdpAdminClient(Protocol):
    async def register_client(
        self, *, client_id: str, client_name: str, service_account: bool
    ) -> ClientRegistrationResult: ...

    async def declare_client_roles(
        self, client_ref: str, roles: list[RoleDefinition]
    ) -> None: ...

@dataclass
class ClientRegistrationResult:
    client_id: str
    internal_ref: str                       # provider-specific handle for follow-up calls
    registration_access_token: str | None    # DCR follow-up management token, if supported

@dataclass
class RoleDefinition:
    name: str
    description: str
    composite_of: list[str] = field(default_factory=list)
```

`KeycloakAdminClient(IdpAdminClient)` is the concrete implementation:
`register_client` POSTs to `{issuer}/clients-registrations/openid-connect`
(Keycloak's OIDC Dynamic Client Registration endpoint) with the supplied
token as Bearer auth; `declare_client_roles` uses the Admin REST API
(`/admin/realms/{realm}/clients/{id}/roles`) to create the role vocabulary
under the newly-registered client, using Keycloak's native composite-role
feature for the 5 bundle roles. A future `Auth0AdminClient`/
`OktaAdminClient` would implement the same `IdpAdminClient` protocol.

**Role vocabulary as client roles, not realm roles.** The 24 leaf scopes +
5 composite roles from the Authentication & Authorization section are
declared as roles namespaced under the newly-registered client (Keycloak's
idiomatic pattern for "this app defines these roles"), defined once as
data in `src/loom/idp/catalog_roles.py` — the single source of truth for
the role graph, consumed by this command. The command's job stops at
declaring the vocabulary; assigning roles to actual end-user/service
accounts is a separate, later action a Keycloak admin performs, outside
this command's scope.

**Command**:

```
loom idp register-client \
  --issuer-url https://keycloak.example/realms/loom \
  --token <initial-access-token> \
  --client-id loom-catalog-api \
  [--client-name "Loom Catalog API"]
```

Registers the client via DCR, declares all 24+5 roles under it, and
prints a summary including the DCR `registration_access_token` (needed
for future client updates — shown once, flagged to store securely).
`--issuer-url` defaults to `config.auth.issuer` if already set.

## New dependencies

- `fastapi>=0.115` — core
- `uvicorn[standard]>=0.32` — core (also the component's runtime server)
- `pyjwt[crypto]>=2.9` — core
- `httpx>=0.27` — core (used by `loom idp register-client` at runtime,
  and as the test client in `tests/`)

## Out of scope / open items carried forward

- The lifecycle-state-mutates-in-place deviation from the physical-model
  spec's original "no in-place UPDATE" wording (flagged above) —
  reconsider if a future audit requirement wants every governance-state
  change to also be a distinct content version.
- Cross-tenant access to catalog *content* is not modeled — content-tier
  requests are always scoped to exactly one tenant, derived from the
  token. Platform-tier scopes (`catalog:tenant:*`, and
  `catalog:principal:write`/`catalog:environment:write` with their
  client-specified `tenant_id`) are the one deliberate exception, by
  design (see Authentication & Authorization) — not an oversight to
  revisit, but worth remembering when deciding who holds those scopes.
- Rate limiting, request-size limits, and other API-gateway-shaped
  concerns are assumed to be handled by infrastructure in front of this
  component (matches `CLAUDE.md`'s "gateway enforces the same RBAC/Policy
  decisions physically" framing), not implemented here.
