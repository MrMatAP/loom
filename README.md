# Loom

An AI-Enabled Distributed Systems IDE

Deploying the Catalog API/MCP server as a container (Docker, Kubernetes,
horizontal scaling)? See [docs/admin-guide.md](docs/admin-guide.md).
Already have one running and just want to use it? See
[docs/user-guide.md](docs/user-guide.md). This README is the full
reference both guides link back into.

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

## Catalog API

The Catalog component is a standalone FastAPI service fronting the
Capability/Agent/Skill/Tool/DataSource/DataProduct/ModelEndpoint
aggregates, plus a platform-bootstrap tier (Tenant/Principal/Environment).
It requires a configured database (see above) and an OIDC issuer:

    loom config set auth.issuer https://idp.example/realms/loom
    loom config set auth.audience loom-catalog-api

Run it:

    loom-catalog-api

Or directly via uvicorn for development: `uvicorn loom.api.catalog.main:app --reload`.

Every request requires a Bearer JWT carrying a `tenant_id` claim and
either a `scope` claim (space-separated `catalog:{resource}:{action}`
scopes) or a `roles` claim (`catalog-viewer`, `catalog-editor`,
`catalog-approver`, `catalog-admin`, `catalog-platform-admin`). See
`docs/superpowers/specs/2026-08-08-catalog-api-design.md` for the full
access-rights model.

### Registering the client with your IDP

For Keycloak, authenticate as an admin (the standard Keycloak superadmin
realm is `master`):

    loom idp register-client --issuer-url https://idp.example/realms/loom \
      --client-id loom-catalog-api

You'll be prompted for the admin username and password. To avoid the
prompt, set `LOOM_IDP_ADMIN_USERNAME`/`LOOM_IDP_ADMIN_PASSWORD`, or pass
`--admin-username`/`--admin-password` directly (flags take precedence over
env vars, which take precedence over the prompt). If the admin account
lives in a different realm, or the deployment uses a different admin
client than the default `admin-cli`, pass `--admin-realm`/`--admin-client-id`.

This registers the OAuth client via the Keycloak Admin REST API and
declares all 27 leaf scopes plus the 5 composite roles (`catalog-viewer`,
`catalog-editor`, `catalog-approver`, `catalog-admin`,
`catalog-platform-admin`) as roles under that client, printing the
generated client secret (store it securely — it is shown once). It also
sets `auth.issuer`/`auth.audience` in the local config to the values it
just registered (overwriting whatever was there before), so the manual
`loom config set auth.issuer`/`auth.audience` step above isn't needed when
you run this command. It also resets `auth.jwks_uri` to unset so it
re-derives from the new issuer — if you had pinned it (e.g. an internal
issuer URL behind a proxy with a different external JWKS endpoint),
re-pin it after running this command. Assigning those roles to actual users/service
accounts is a separate step performed in the Keycloak admin console.

### Interactive login via Swagger UI

The Catalog API's `/docs` page can drive a real Authorization Code + PKCE
login against your IDP instead of requiring you to paste in a bearer token
by hand. Register a public client for it (needs `auth.audience` already
set — run `loom idp register-client` first):

    loom idp register-docs-client --issuer-url https://idp.example/realms/loom \
      --client-id loom-docs --api-base-url https://api.example.com

This sets `auth.docs_client_id` in the local config; `loom-catalog-api`
picks it up on next start and Swagger UI's "Authorize" button appears.
Click it, log in against your IDP, and every "Try it out" call on `/docs`
from then on carries a real bearer token — no manual header-pasting.
`--api-base-url` must be the externally-reachable URL the browser itself
loads `/docs` from (it's turned into the OAuth redirect URI
`{api_base_url}/docs/oauth2-redirect`); a mismatch here is the most common
reason the login redirect fails.

### CLI device-code login

`loom` commands that need to call the Catalog API as a user (e.g.
`loom capability create`) authenticate via the OAuth2 Device Authorization
Grant (RFC 8628) — no local browser redirect target needed, so it also
works over SSH. Register the device-flow client once (same prerequisite as
above — `auth.audience` must already be set):

    loom idp register-cli-client --issuer-url https://idp.example/realms/loom \
      --client-id loom-cli

Then log in:

    loom auth login

This prints a verification URL and a short user code (or a single
`verification_uri_complete` link where the IDP supports it); open it in
any browser — including on a different device — and approve the login.
`loom auth login` polls in the background and, once approved, caches the
access/refresh tokens in the local config (file permissions hardened to
`0600`, tokens masked whenever the config is printed). Check status with
`loom auth status`, and clear the session with `loom auth logout`.

**Session length**: how long that login lasts before every `loom`
subcommand demands a fresh one is Keycloak's realm/client "Access Token
Lifespan" (commonly a few minutes by default) — `loom` doesn't shorten it,
but it doesn't lengthen it either: the cached `refresh_token` is stored but
not yet used to silently renew an expired session, so a lapsed session
currently means a full `loom auth login` again, not a quiet refresh. If the
realm default feels too short, override it for just this client at
registration time (or re-run against an already-registered one to change
it later):

    loom idp register-cli-client --issuer-url https://idp.example/realms/loom \
      --client-id loom-cli --access-token-lifespan 1800

(seconds; also settable via `LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN`, flag
takes precedence). This changes only `loom-cli`'s own token lifespan, not
the realm default other clients rely on.

### Managing Catalog entities from the CLI

Once logged in (see above), `loom capability`, `loom model`, and
`loom agent` talk straight to the running Catalog API using the cached
session token, rendered as `rich` tables (an `openstack`/`ipa`-style feel:
a Field/Value table for a single entity, a columnar table for a list).
Point them at the API once (defaults to `http://localhost:8000`):

    loom config set catalog.api_base_url https://api.example.com

Each of the three resources gets the same six verbs:

    loom capability create <slug> <name> [--description ...] [--target-metrics '[...]'] [--owner-id ...]
    loom capability list [--lifecycle-state ...] [--slug ...] [--limit N] [--offset N]
    loom capability show <entity_id> [--version N]
    loom capability update <entity_id> <slug> <name> [same flags as create]
    loom capability versions <entity_id>
    loom capability transition <entity_id> <version> <to_state>

`model`/`agent` take the same six verbs with their own resource-specific
`create`/`update` flags (see `--help` on each). Examples:

    loom capability create latency-slo "Latency SLO" \
      --target-metrics '[{"name": "p99_latency_ms", "target": 200}]'

    loom model create claude-opus "Claude Opus" \
      --protocol anthropic_messages --model claude-opus-4

    loom agent create triage-bot "Triage Bot" \
      --layer business_tech --memory-scope session \
      --model-binding-id <entity_id from `loom model create` above> \
      --prompt "You triage incoming support tickets."

    loom capability list --lifecycle-state draft
    loom capability show <entity_id>
    loom capability show <entity_id> --version 1
    loom capability transition <entity_id> 1 in_review

This registry is append-only/versioned by design (see
`docs/superpowers/specs/2026-08-08-catalog-api-design.md`), so `update`
means "create a new version row", not an in-place mutation -- it takes
the *entire* set of create flags again (including unchanged ones), and
there's no `delete`. `--owner-id` defaults to the caller; JSON-shaped
flags (`--target-metrics`, `--llm-config`, `--permission-boundary`) default
to an empty array/object. Every command prints its result as a table on
success; scripting a chain (e.g. an Agent's `--model-binding-id`) means
reading the `entity_id` column back out of a prior command's output.

### Managing Tenants and Principals from the CLI

`loom tenant`/`loom principal` work the same way, against the same API,
but aren't `VersionedEntity` -- no `lifecycle_state`/version history, so
there's `create`/`list`/`show`/`update` and no `versions`/`transition`,
and `update` is a real in-place `PATCH` rather than a new version:

    loom tenant create <slug> <name>
    loom tenant list [--limit N] [--offset N]
    loom tenant show <tenant_id>
    loom tenant update <tenant_id> <name>

    loom principal create --tenant-id <id> --kind {user,agent,service_account} \
      --display-name <name> --external-id <sub>
    loom principal list --tenant-id <id> [--limit N] [--offset N]
    loom principal show <principal_id>
    loom principal update <principal_id> <display_name>

`--external-id` is the identity a token has to carry (its `sub` claim) for
`resolve_principal` to match it up at request time -- see
`src/loom/api/catalog/dependencies.py`. Neither resource has a `delete`
endpoint.

**Bootstrapping the first one.** `loom principal create` calls `POST
/principals`, which itself requires an already-authorized, already-
provisioned caller -- on a fresh deployment nobody is one yet, so this
command can't create its own prerequisite. `loom db seed-principal` is the
escape hatch: it writes the Tenant/Principal rows directly to the database
via `config.database` (the same direct-DB-access story as `loom db
upgrade`), no bearer token involved, entirely bypassing the API/Policy
Engine:

    loom db seed-principal --tenant-slug acme --tenant-name "Acme Corp" \
      --kind user --display-name "Mathieu Imfeld" --external-id <sub from the IDP token>

Safe to re-run: it reuses an existing Tenant by `--tenant-slug` (only
needs `--tenant-name` the first time), and reports rather than erroring if
a Principal for that `(tenant, external_id)` pair already exists. It also
writes one `AuditEvent` for the bootstrap itself (`actor_principal_id`
pointing at the Principal it just created, since there's no other actor to
attribute it to), so the bypass isn't silent -- but every *subsequent*
Principal should go through `loom principal create` instead, so it's
actually policy-checked and audit-logged the normal way. This only creates
the database row -- the matching IDP account still separately needs a
`tenant_id` attribute set to this Tenant's id (see `docs/admin-guide.md`'s
Troubleshooting section), or its tokens still won't carry the claim this
Principal is resolved by.

### TLS trust for the IDP connection

Every outbound call to the IDP (`loom idp ...`, `loom auth login`, JWKS
resolution, OIDC discovery) goes through `loom.tls.build_ssl_context()`,
which defaults to the OS-native trust store. If your IDP's certificate is
issued by a CA that's trusted system-wide but isn't visible to that
OS-trust-store lookup on your platform (observed on macOS with a CA
installed via a management tool into the login keychain rather than the
System roots), set an explicit override instead of fighting the OS store:

    export LOOM_IDP_CA_BUNDLE=/path/to/ca-bundle.pem
    loom idp register-client ...

### Verifying interactively

Both flows above are exercised automatically against a real IDP by
`tests/integration/` (see below) short of the one step neither can
automate without a browser: a human approving the login. To confirm that
step works end to end at least once after registering clients on a new
IDP instance, do it by hand — open `/docs`, click Authorize, log in; then
run `loom auth login` and approve the printed link. If either fails, the
registration commands' output (redirect URI, client ID) is the first
place to check against what actually loaded in the browser — a mismatched
redirect URI shows up as Keycloak's own `invalid_redirect_uri` error page
mid-flow, not a silent failure back on `/docs`.

### Live IDP integration tests

`tests/integration/` runs the checks above against a real Keycloak
instance instead of a mocked transport, and is excluded from the default
`pytest` run (it self-skips, so `pytest` alone stays fast and needs no
live credentials). To run it:

    export LOOM_IDP_ISSUER_URL=https://idp.example/realms/loom
    export LOOM_IDP_ISSUER_ADMIN_USERNAME=admin
    export LOOM_IDP_ISSUER_ADMIN_PASSWORD=<secret>
    # only if your OS trust store doesn't see the IDP's CA -- see above
    export LOOM_IDP_CA_BUNDLE=/path/to/ca-bundle.pem

    pytest tests/integration/ -m live_idp

Three tiers, gated independently:

- **`test_discovery.py`** needs only `LOOM_IDP_ISSUER_URL` — confirms the
  issuer is reachable and that `security.py`'s Keycloak-conventional
  endpoint derivation matches what the instance actually advertises.
- **`test_swagger_login.py`** / **`test_cli_device_flow.py`** need admin
  credentials too — register throwaway `loom-it-*` clients (mirroring
  `register-docs-client`/`register-cli-client`) and confirm their
  Keycloak-side config and the app's own OpenAPI/Swagger wiring line up
  with the live discovery document; the device-flow test also drives one
  real `start()`/`poll()` round trip and confirms it reports RFC 8628's
  `authorization_pending`.
- **`test_claim_chain.py`** is the deepest check: it registers a
  throwaway *password-grant* client purely to pull a real signed token to
  inspect (Swagger/CLI never use this grant — `directAccessGrantsEnabled`
  stays hardcoded `False` on both, and this stays true after running these
  tests), decodes it with the real `TokenValidator` against the live
  JWKS, and runs a real bearer token through an in-process FastAPI app to
  confirm the audience/roles/`tenant_id` protocol mappers produce a token
  shape `expand_claims_to_scopes`/`get_current_principal` actually accept.

Every object these tests create in the live realm is prefixed `loom-it-`
and deleted at the end of the run. If a run is interrupted between setup
and teardown (killed mid-run, machine sleeps), a `loom-it-*` object can be
left behind — worth a manual check after an interrupted run, particularly
for `loom-it-claims-probe`, since it's the one object in this suite with
`directAccessGrantsEnabled` on. If the admin credentials are rejected,
the whole suite skips with the exact Keycloak error rather than failing —
that's a credentials problem to fix in your environment, not a test bug.

### Live Postgres integration tests

A second, independent live suite exercises a real Postgres instance
instead of the in-memory sqlite the rest of `pytest` uses — migrated via
the packaged Alembic revisions (`loom db upgrade`'s own code path), so it
catches things sqlite's looser typing can hide (native UUID/enum columns,
constraints). Also self-skips without live credentials:

    export LOOM_DB_HOST=localhost
    # LOOM_DB_PORT/LOOM_DB_NAME/LOOM_DB_USERNAME/LOOM_DB_PASSWORD default
    # the same way they do for `loom db upgrade` itself -- see
    # docs/admin-guide.md's configuration table.

    pytest tests/integration/ -m live_db

Point it at a disposable database, not production: migrations are applied
and left in place (additive schema, same reasoning as the IDP suite's
`tenant_id` User Profile attribute), and each test runs inside a
transaction rolled back on exit so no row it writes persists — but neither
of those makes it something to run against a database anything else
depends on.

### Assigning an Agent to an LLM model

An `Agent` doesn't carry model connection details directly. It binds, via
`model_binding_id`, to a `ModelEndpoint` — a separate versioned,
governed entity describing *how to reach* a model (protocol, base URL,
model identifier, credential-vault binding). This lets several Agents
share one endpoint and lets that endpoint carry its own
lifecycle/approval independent of any Agent using it.

Register a generic OpenAI-compatible endpoint (e.g. a self-hosted vLLM or
Ollama server):

    POST /api/v1/model-endpoints
    {
      "slug": "self-hosted-llama",
      "name": "Self-Hosted Llama",
      "protocol": "openai_compatible",
      "base_url": "https://llm.internal.example/v1",
      "model": "meta-llama/Llama-3-70b"
    }

`base_url` is required when `protocol` is `openai_compatible` (enforced by
a CHECK constraint); it's optional for `anthropic_messages`, which has a
well-known default endpoint. `auth_binding_id` points into the same
credential vault as `Tool.auth_binding_id`/`DataSource.connection_binding_id`
— never put a raw API key in the request body.

Then bind an Agent to it by the returned `entity_id` (not `id` — the
binding floats to whichever version is currently `is_current`, unlike
`ToolDataBinding.datasource_id`'s version-pinned `id` reference):

    POST /api/v1/agents
    {
      "slug": "triage-agent",
      "name": "Triage Agent",
      "layer": "business_ops",
      "model_binding_id": "<model-endpoint entity_id>",
      "llm_config": {"temperature": 0.2},
      "prompt": "You triage incoming tickets.",
      "memory_scope": "session"
    }

`llm_config` stays on the Agent for per-agent invocation overrides
(temperature, max_tokens, ...); the endpoint's identity and transport live
on `ModelEndpoint`, not here.

## Catalog MCP server

An alternative interface onto the same use-cases as the REST API above
(`Capability`, `ModelEndpoint`, `Agent`) — exposed as MCP tools instead of
HTTP endpoints, so an Agent can invoke them directly. It shares the REST
API's `database`/`auth` config and calls the exact same Service layer
(`CapabilityService`/`ModelEndpointService`/`AgentService`), not a proxy
over HTTP — see `docs/superpowers/specs/2026-08-08-catalog-api-design.md`.
Run it:

    loom-catalog-mcp

This serves Streamable HTTP on `http://0.0.0.0:8100/mcp` (a different
port than `loom-catalog-api`'s 8000, since both can run at once). Point
any MCP client at it with the same kind of bearer JWT the REST API
expects — issued by the same IDP client, carrying the same
`tenant_id`/`roles`/`scope` claims (see `loom idp register-client` above)
— and it enforces the identical tenant resolution and
`catalog:{capability,model_endpoint,agent}:{read,write,transition}` scope
checks the REST routes do; there's no separate, weaker MCP auth path.

Seven tools are registered per resource (21 total), one per REST
endpoint, taking the same request shape as the JSON bodies documented
above — using `capability` as the example, `model`/`agent` follow the
same pattern:

- `create_capability(data: CapabilityCreateRequest) -> CapabilityRead`
- `get_capability(entity_id) -> CapabilityRead`
- `list_capabilities(lifecycle_state?, slug?, limit=50, offset=0) -> Page[CapabilityRead]`
- `list_capability_versions(entity_id) -> list[CapabilityRead]`
- `get_capability_version(entity_id, version) -> CapabilityRead`
- `update_capability(entity_id, data: CapabilityCreateRequest) -> CapabilityRead`
- `transition_capability(entity_id, version, to_state) -> CapabilityRead`

Same no-delete, append-only-versioning story as the CLI above: `update_*`
creates a new version row, not an in-place mutation.
