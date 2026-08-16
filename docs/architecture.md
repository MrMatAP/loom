# Architecture

How the Catalog API, MCP server, and CLI fit together and why they're
shaped the way they are -- the developer-facing counterpart to the
strictly user-facing admin/user guides. For the domain vocabulary
(Capability, Agent, Skill, ...) and the invariants code in this repo is
expected to enforce, see [CLAUDE.md](../CLAUDE.md) -- that document is the
source of truth for the entity model; this one covers what's actually
implemented, how the pieces talk to each other, and the internal
implementation detail and test coverage behind operational instructions
that live in the two guides. For installing/operating the services see
[docs/admin-guide.md](admin-guide.md); for driving an already-running one
see [docs/user-guide.md](user-guide.md).

## Services in one paragraph

Both the Catalog API and the Catalog MCP server are stateless HTTP
processes (FastAPI/Uvicorn) backed by a shared PostgreSQL database and a
shared external OIDC issuer -- neither holds request-scoped state anywhere
but the DB, and the MCP server runs with `stateless_http=True` specifically
so no session is pinned to one process either (see
`src/loom/api/catalog/mcp/main.py`). That's what makes horizontal scaling a
replica-count knob rather than a code-level concern: any request can go to
any replica. One container image serves both processes plus the `loom` CLI
(used for one-shot jobs like `loom db upgrade`), selected by which command
runs, not by building three images.

## What's implemented

The registry models Capability, Agent, Skill, Tool, DataSource,
DataProduct, and ModelEndpoint as `VersionedEntity` rows, plus a
platform-bootstrap tier (Tenant, Principal, Environment) that isn't
versioned. All ten have SQLAlchemy models and REST routers. Every
`VersionedEntity` (`loom capability|model|agent|skill|tool|datasource
|dataproduct`, plus each one's own sub-resource verbs -- `skill node|
edge`, `tool binding`, `dataproduct lineage`) and Environment (`loom
environment`, CRUD, not versioned) now also has CLI and MCP tool coverage.
Tenant and Principal remain REST+CLI only -- they're bootstrapping routes
with a different auth shape (no already-provisioned Principal required;
see `cli/catalog.py`'s Tenant/Principal section) that the MCP server
deliberately doesn't expose. Evaluation, governance (beyond the audit
log), and observability (Trace/Metric) exist as SQLAlchemy models
(`src/loom/model/evaluation.py`, `governance.py`, `observability.py`)
with no API surface yet.

## Versioning and lifecycle

The registry is append-only by design: there is no delete on any resource,
and `update` creates a new version row rather than mutating the current
one in place -- it takes the *entire* create payload again, including
unchanged fields. Every entity moves through `draft -> in_review ->
approved -> published -> deprecated -> retired` one step at a time; the
API rejects a transition that skips a state rather than silently applying
it.

## Multi-tenancy and Principal resolution

There is no `tenant_id` claim anywhere in this system -- not on a token,
not as an IDP account attribute. Every route but `/tenants` itself is
nested under a specific Tenant in the URL path
(`/api/v1/tenants/{tenant_id}/...`), and `get_current_principal`
(`src/loom/api/catalog/dependencies.py`) resolves access by looking up the
one `Principal` row whose `external_id` matches the token's `sub` claim
*within that path's `tenant_id`* -- never through a header or claim hint.
This is deliberate: onboarding a user is one action (`loom principal
create`), not two (create the Principal, and separately remember to tag
their IDP account), and it turns "which Tenant is this request for" into
an explicit, auditable part of every request rather than an implicit trust
decision.

One consequence: `external_id` is only unique *per Tenant*
(`uq_principal_tenant_external_id` in `src/loom/model/tenant.py`), so the
same `sub` can legitimately be provisioned in more than one Tenant -- a
consultant working across two customer Tenants, for instance. `GET
/tenants/mine` lists which Tenants a caller can act in (every Tenant for a
platform admin, i.e. anyone whose token carries `catalog:tenant:read`;
otherwise only the Tenants where they already have a Principal) so a
client can choose *before* making a Tenant-scoped request. The CLI
(`loom auth login`) drives this automatically -- see
[docs/admin-guide.md](admin-guide.md#authorization-model) -- and stores the
chosen `tenant_id` client-side, embedding it directly into the URL path of
every subsequent request (`CatalogClient.tenant_path`); it is never sent
as a header, and the server never trusts a client-supplied hint over the
URL path it's already resolving against. The MCP server takes the same
approach: `tenant_id` is a required argument on every tool (see "Catalog
MCP server" below), not a header, so an MCP client must pass it explicitly
on every call.

An invalid/expired token (`AuthenticationError`) is a `401`; a valid token
with no matching Principal in the requested Tenant
(`PrincipalNotInTenantError`) is a `403`, same as an insufficient scope
(`InsufficientScopeError`) -- the token itself is fine in both `403`
cases, so re-authenticating doesn't help either. See
[docs/admin-guide.md](admin-guide.md#troubleshooting).

## Authorization: scopes and roles

Every content-tier request requires a scope of the form
`catalog:{resource}:{action}` -- `resource` is one of `capability`,
`agent`, `skill`, `tool`, `datasource`, `entity`, `model_endpoint`
(`read`/`write`/`transition`); the platform tier adds `tenant`,
`principal`, `environment` (`read`/`write` only). `src/loom/idp/
catalog_roles.py` is the single source of truth for this vocabulary and
for the five composite roles that bundle it (`catalog-viewer` through
`catalog-platform-admin` -- see
[docs/admin-guide.md](admin-guide.md#authorization-model) for what each
grants). A token can carry either a raw `scope` claim (space-separated) or
a `roles` claim naming these bundles; `expand_claims_to_scopes` flattens
either into the same scope set, so the API and CLI never need to reason
about roles vs. scopes separately.

A missing scope is a `403` (`InsufficientScopeError`), not a `401` -- the
token is fine, it just doesn't authorize this call. `POST /tenants` and
`POST /tenants/{tenant_id}/principals` are the one exception to the
content-tier pattern: they only check `catalog:tenant:write`/
`catalog:principal:write` and never call `get_current_principal`, which is
what lets a platform administrator bootstrap a Tenant/Principal before any
Principal row exists for them at all.

## OAuth client topology

Four OAuth clients, registered together by `loom idp register` in
dependency order:

1. **RESTful API** -- confidential, with a service account. Declares all 27
   leaf scopes plus the 5 composite roles as roles under this one client --
   the single source every other client's tokens read from.
2. **MCP server** -- confidential, its own resource-server client with its
   own audience (`auth.mcp_audience`), so `loom-catalog-mcp` validates
   tokens against a different `aud` than `loom-catalog-api` does.
   Deliberately gets no role declarations of its own.
3. **Swagger UI** -- public, Authorization Code + PKCE, for interactive
   login from `/docs`.
4. **CLI** -- public, OAuth2 Device Authorization Grant, for `loom auth
   login`.

The two public clients each get an audience mapper for *both*
resource-server clients (so a CLI login token authorizes both REST and MCP
calls without a second login) and a client-roles mapper pointed at the API
client (the role vocabulary is declared once, not duplicated per client).
See [docs/admin-guide.md](admin-guide.md#loom-idp-register-reference) for
the command itself.

## ModelEndpoint / Agent binding

An `Agent` doesn't carry model connection details directly; it binds, via
`model_binding_id`, to a `ModelEndpoint` -- a separate versioned, governed
entity describing *how to reach* a model (protocol, base URL, model
identifier, credential-vault binding). This lets several Agents share one
endpoint and lets the endpoint carry its own lifecycle/approval
independent of any Agent using it. `ModelEndpoint.auth_binding_id` points
into the same credential vault as `Tool.auth_binding_id`/
`DataSource.connection_binding_id` -- never put a raw API key in a
ModelEndpoint request body. The binding is **floating**: it holds
the ModelEndpoint's `entity_id` and always resolves to whichever version is
currently `is_current`, rather than pinning to one version's row `id` --
`entity_id` alone can't back a DB-level foreign key, which is a known,
deliberately-accepted trade-off (see CLAUDE.md's open design decisions for
the same question applied to `Tool.data_bindings[]`, which leans the other
way). `llm_config` stays on the Agent for per-invocation overrides
(temperature, max_tokens, ...); the endpoint's identity and transport live
on `ModelEndpoint`, not there.

## Catalog MCP server

An alternative interface onto the same use-cases as the REST API
(`Capability`, `ModelEndpoint`, `Agent`) -- exposed as MCP tools instead of
HTTP endpoints, so an Agent can invoke them directly. It shares the REST
API's `database` config and calls the exact same Service layer
(`CapabilityService`/`ModelEndpointService`/`AgentService`), not a proxy
over HTTP -- see
`docs/superpowers/specs/2026-08-08-catalog-api-design.md`. It enforces the
identical `catalog:{resource}:{read,write,transition}` scope checks the
REST routes do; there is no separate, weaker MCP auth path.

Seven tools are registered per resource (21 total), one per REST endpoint,
each taking `tenant_id` as a required first argument (the same role the
URL path plays on the REST side -- see "Multi-tenancy" above):
`create_X`, `get_X`, `list_Xs`, `list_X_versions`, `get_X_version`,
`update_X`, `transition_X`.

## Audit trail

`POST /tenants` and `POST /tenants/{tenant_id}/principals` both write an
`AuditEvent` (`tenant.create`/`principal.create`) regardless of whether the
caller has a resolvable Principal yet -- attributed to the caller's
Principal when they have one, or to their token's `sub` claim in
`details.actor_external_id` when they don't (a platform administrator, on
the very first bootstrap most notably; see
`src/loom/api/catalog/audit.py`). The one write that is *not* audited is
`loom db upgrade`'s default-Tenant seed (`_seed_default_tenant`,
`src/loom/cli/db.py`) -- it runs before any login, so there is no `sub` to
attribute it to, and unlike the two calls above it grants no identity
access by itself.

## Configuration and secrets

Every container reads its configuration from a YAML file at
`$LOOM_CONFIG_PATH`, assembled from plain environment variables by
`docker/entrypoint.sh` at container start (see
[docs/admin-guide.md](admin-guide.md#configuration) for the variable
reference an admin actually sets). Two implementation details explain why
that file has to live on writable storage, not a mounted Secret:

- `RootConfig.save()` -- which `loom config set` round-trips through, and
  which every `loom` invocation calls, not just `config set` --
  `chmod(0o600)`s the file it just wrote. A read-only-mounted Secret can't
  be `chmod`'d, so `entrypoint.sh` instead writes the assembled file into a
  path the container owns itself (a small `emptyDir`, which still works
  under `readOnlyRootFilesystem: true`).
- `RootConfig.load()` requires the file to contain a `config_path` key
  matching its own path. A file written by `loom config set`/
  `entrypoint.sh` always has one; a hand-authored file generally won't and
  fails validation as a result.

TLS trust for outbound IdP calls (`loom idp ...`, `loom auth login`, JWKS
resolution, OIDC discovery) goes through `loom.tls.build_ssl_context()`,
which defaults to the OS-native trust store and honors
`LOOM_IDP_CA_BUNDLE` as an explicit override -- see
[docs/admin-guide.md](admin-guide.md#tls-trust-for-the-idp-connection) for
when you need it.

## Deployment topology

Sandbox, Staging, and Production are meant to be physically isolated
environments (separate compute + network boundaries), not a config flag,
per CLAUDE.md's structuring principles -- the `Environment` entity records
this at the registry level, but provisioning the isolation itself is
outside what's built here today. See
[docs/admin-guide.md](admin-guide.md#operate) for the concrete Docker
Compose / Kubernetes topology this repo ships.

## Testing

`uv run pytest` runs the full default suite against an in-memory sqlite DB
and a mocked IdP transport -- fast, no external dependencies. Three live
suites are excluded by default (self-skip without credentials/a running
service) and exercise a real Keycloak instance, a real Postgres instance,
and a real OpenAI-compatible LLM server respectively.

### Live IdP integration tests

`tests/integration/` runs the Swagger/CLI login flows and a claims round
trip against a real Keycloak instance instead of a mocked transport, and
is excluded from the default `pytest` run (self-skips without live
credentials). `LOOM_IDP_CA_BUNDLE` is only needed if your IdP's CA isn't
in the OS trust store -- see
[docs/admin-guide.md](admin-guide.md#tls-trust-for-the-idp-connection):

```
export LOOM_IDP_ISSUER_URL=https://idp.example/realms/loom
export LOOM_IDP_ISSUER_ADMIN_USERNAME=admin
export LOOM_IDP_ISSUER_ADMIN_PASSWORD=<secret>
export LOOM_IDP_CA_BUNDLE=/path/to/ca-bundle.pem   # only if needed

pytest tests/integration/ -m live_idp
```

Three tiers, gated independently:

- **`test_discovery.py`** needs only `LOOM_IDP_ISSUER_URL` -- confirms the
  issuer is reachable and its discovery document advertises what
  `security.py` needs plus the device code grant.
- **`test_swagger_login.py`** / **`test_cli_device_flow.py`** need admin
  credentials too -- register throwaway `loom-it-*` clients and confirm
  their Keycloak-side config lines up with the live discovery document;
  the device-flow test drives one real `start()`/`poll()` round trip --
  the same login flows checked by hand in
  [docs/admin-guide.md](admin-guide.md#verifying-interactively).
- **`test_claim_chain.py`** registers a throwaway *password-grant* client
  purely to pull a real signed token to inspect (Swagger/CLI never use
  this grant), decodes it with the real `TokenValidator` against the live
  JWKS, and runs a real bearer token through an in-process FastAPI app.

Every object these tests create is prefixed `loom-it-` and deleted at the
end of the run. If a run is interrupted between setup and teardown, a
`loom-it-*` object can be left behind -- worth a manual check afterward,
particularly `loom-it-claims-probe`, the one object in this suite with
direct access grants enabled. If admin credentials are rejected, the whole
suite skips with the Keycloak error rather than failing.

### Live Postgres integration tests

A second, independent live suite exercises a real Postgres instance
instead of the in-memory sqlite the rest of `pytest` uses, migrated via
the packaged Alembic revisions -- catches things sqlite's looser typing
can hide. Also self-skips without live credentials.
`LOOM_DB_PORT`/`LOOM_DB_NAME`/`LOOM_DB_USERNAME`/`LOOM_DB_PASSWORD` default
the same way they do for `loom db upgrade` -- see
[docs/admin-guide.md](admin-guide.md#configuration):

```
export LOOM_DB_HOST=localhost

pytest tests/integration/ -m live_db
```

Point it at a disposable database, not production: migrations are applied
and left in place, and each test runs inside a transaction rolled back on
exit -- but neither of those makes it safe to run against a database
anything else depends on.

### Live LLM integration test

A third, independent live suite proves a Capability/ModelEndpoint/Agent
created through the real Catalog REST routes actually wires up to a real
model: it drives an in-memory Catalog (Keycloak/Postgres aren't the live
dependency here -- `test_claim_chain.py` already covers the IdP path), then
hands the created `ModelEndpoint` to a real
[LangChain](https://python.langchain.com/) agent
(`langchain.agents.create_agent`, LangGraph-backed under the hood) and
asserts it gets a real reply back. Self-skips (with a specific reason)
when nothing answers, when a server answers but has no model loaded, or
when the `live-llm` dependency group isn't installed:

```
uv sync --group live-llm

# LOOM_LLM_BASE_URL defaults to http://localhost:1234 (LM Studio's
# default) -- override it for vLLM/ollama/any other OpenAI-compatible
# server. No credentials needed; local servers don't check the API key.
export LOOM_LLM_BASE_URL=http://localhost:1234

pytest tests/integration/ -m live_llm
```

The model actually exercised is whatever the live server reports at
`GET {base_url}/v1/models` -- discovered at test time, never hardcoded,
since there's no way to know what's loaded locally. The `ModelEndpoint`
row itself stores the bare origin (`http://localhost:1234`, no `/v1`); the
test appends the OpenAI-compatible path only when building the LangChain
client -- see `test_live_llm.py`'s module docstring for why that split
isn't yet a documented contract anywhere else in the codebase.
