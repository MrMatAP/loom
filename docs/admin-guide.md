# Catalog Admin Guide

Installing and operating the Catalog API and MCP server, and the
authorization model administrators are responsible for configuring in the
IdP. For what these services *do* and why they're shaped the way they are,
see [docs/architecture.md](architecture.md). For day-to-day usage once
they're running, see [docs/user-guide.md](user-guide.md).

## Install

**Prerequisites**: PostgreSQL 14+ reachable from wherever you run the
containers (provisioning it is out of scope here); an OIDC issuer;
Docker; for Kubernetes, `kubectl`, `kustomize` (or `kubectl apply -k`),
and a `metrics-server` if you want the HorizontalPodAutoscalers to scale.

```
# 1. Build and push the image
docker build --build-arg VERSION=1.4.2 -t ghcr.io/your-org/loom:1.4.2 .
docker push ghcr.io/your-org/loom:1.4.2

# 2. Migrate the database (creates a `default` Tenant on an empty DB)
loom config set database.host db.example.com
loom config set database.username loom
loom config set database.password <secret>
loom db upgrade

# 3. Register the four OAuth clients with your IdP
loom idp register --issuer-url https://idp.example/realms/loom \
  --client-id loom-catalog-api --api-base-url https://api.example.com

# 4. Deploy (pick one)
docker compose up --build                    # local / trial
kubectl apply -k deploy/kubernetes            # production, see "Operate" below

# 5. Grant one human the catalog-platform-admin role in your IdP,
#    then provision their Principal -- see "Authorization model" below.
```

See "Operate" and "Reference" below for the detail behind each step.

## Operate

### Running locally with Docker Compose

`docker-compose.yaml` at the repo root runs Postgres, a one-shot `loom db
upgrade` migration, and single instances of both services -- useful for
trying the Catalog out or developing against it, not a horizontal-scaling
story (that's Kubernetes below).

```
cp .env.example .env   # fill in LOOM_DB_PASSWORD, LOOM_AUTH_ISSUER, LOOM_AUTH_AUDIENCE
docker compose up --build
```

The `migrate` service must exit 0 before `catalog-api`/`catalog-mcp` start
(`depends_on: condition: service_completed_successfully`) -- Compose
enforces that ordering for you, unlike the Kubernetes path below.

### Deploying to Kubernetes

Manifests live under `deploy/kubernetes/`, composed via
[Kustomize](https://kustomize.io). `secret.example.yaml` and
`ingress.example.yaml` are templates, not applied resources -- copy and
fill them in; **never commit a filled-in copy**.

1. **Point the manifests at your build**:

   ```
   cd deploy/kubernetes
   kustomize edit set image loom=ghcr.io/your-org/loom:1.4.2
   ```

2. **Edit `configmap.yaml`** -- every `CHANGEME` must be replaced, in
   particular `LOOM_DB_HOST` (no Postgres ships in this manifest set) and
   `LOOM_AUTH_ISSUER`.

3. **Create the database credential Secret**:

   ```
   kubectl create namespace loom-catalog
   kubectl create secret generic loom-db-credentials \
     --namespace loom-catalog \
     --from-literal=password='<your Postgres password>'
   ```

   (Or manage it through your own GitOps/sealed-secrets/external-secrets
   pipeline -- `secret.example.yaml` is copy-paste starting material.)

4. **Apply everything, then confirm the migration finished**:

   ```
   kubectl apply -k .
   kubectl wait --for=condition=complete job/loom-db-migrate \
     --namespace loom-catalog --timeout=120s
   ```

   `loom-db-migrate` runs `loom db upgrade` exactly once, as a dedicated
   Job rather than a per-pod initContainer -- running that command
   concurrently from multiple starting replicas races on a shared Postgres
   enum type. `kubectl apply -k` gives no ordering guarantee between a Job
   and a Deployment, so the `kubectl wait` step isn't optional: API/MCP
   pods can and will start before the schema is ready. That's not silently
   broken -- `/healthz` doesn't touch the database, so those pods report
   Ready and simply error on real requests until migration completes, no
   restart needed once it does. Verify with:

   ```
   loom capability list   # against this deployment's catalog.api_base_url
   # or, without a logged-in CLI session:
   kubectl logs job/loom-db-migrate --namespace loom-catalog
   ```

**Rolling out a new version**: Jobs are immutable, so delete the old
completed one first:

```
kustomize edit set image loom=ghcr.io/your-org/loom:1.5.0
kubectl delete job/loom-db-migrate --namespace loom-catalog --ignore-not-found
kubectl apply -k .
kubectl wait --for=condition=complete job/loom-db-migrate --namespace loom-catalog --timeout=120s
```

The Deployments themselves roll normally -- no special handling needed.

**Scaling**: `catalog-api-hpa.yaml`/`catalog-mcp-hpa.yaml` target 70%
average CPU between 2 and 10 replicas each; needs metrics-server. To scale
manually instead:

```
kubectl scale deployment/loom-catalog-api --namespace loom-catalog --replicas=4
```

`pdb.yaml` keeps at least 1 replica of each up through voluntary
disruptions once you're running more than one -- the default.

**Exposing it externally**: `ingress.example.yaml` is a starting
template -- copy it, fill in `ingressClassName`/host/TLS. Whatever hostname
you land on for the REST API must match `LOOM_CATALOG_API_BASE_URL` in
`configmap.yaml`, and if you want Swagger UI's interactive login to work
through it, must also match the `--api-base-url` you registered via `loom
idp register` -- a mismatch here is the most common reason that login
redirect fails.

### Platform administrator

Every other identity the Catalog knows about -- a Tenant's own users,
agents, service accounts -- is a `Principal` row scoped to one Tenant. The
**platform administrator** is the one exception: an IdP account holding
`catalog-platform-admin` and no Tenant scope, used to create and manage
Tenants and Principals across the whole deployment. It's what onboards the
very first Tenant, so it can't itself depend on one existing yet. See
"Authorization model" below for how to grant this role, and for the
one-time bootstrap sequence.

### TLS trust for the IdP connection

Every outbound call to the IdP (`loom idp ...`, `loom auth login`, JWKS
resolution, OIDC discovery) goes through `loom.tls.build_ssl_context()`,
which defaults to the OS-native trust store. If your IdP's certificate is
issued by a CA that's trusted system-wide but isn't visible to that
OS-trust-store lookup on your platform (observed on macOS with a CA
installed via a management tool into the login keychain rather than the
System roots), set an explicit override instead of fighting the OS store:

```
export LOOM_IDP_CA_BUNDLE=/path/to/ca-bundle.pem
loom idp register ...
```

Inside a container this affects JWKS resolution at request-validation
time too, not just CLI commands -- mount the CA bundle (a ConfigMap volume
works well) and set `LOOM_IDP_CA_BUNDLE` to its path.

### Verifying interactively

Both the Swagger UI and CLI login flows are exercised automatically by
`tests/integration/` (see "Live IdP integration tests" below) short of the
one step neither can automate without a browser: a human approving the
login. To confirm that step works end to end at least once after
registering clients on a new IdP instance, do it by hand -- open `/docs`,
click Authorize, log in; then run `loom auth login` and approve the
printed link. If either fails, the registration command's own output
(redirect URI, client ID) is the first thing to check against what
actually loaded in the browser -- a mismatched redirect URI shows up as
your IdP's own `invalid_redirect_uri` error page mid-flow, not a silent
failure back on `/docs`.

### Troubleshooting

- **Pods `CreateContainerConfigError`** -- almost always a missing/misnamed
  Secret or key (`kubectl describe pod` names the exact key it couldn't
  find).
- **Pod stuck `CrashLoopBackOff` with entrypoint.sh's own log lines** (not
  a Python traceback) -- read them, they name the exact problem (unwritable
  config path, missing DB password); see "Configuration" below.
- **Pods `Running`/`Ready` but every request 500s** -- migration hasn't
  completed yet, or completed against the wrong database. Check `kubectl
  get job/loom-db-migrate` and its logs first.
- **Pod crashes on boot with "Could not reach the OIDC discovery
  endpoint"** -- `auth.discovery_url` (`LOOM_AUTH_DISCOVERY_URL`) doesn't
  respond. This is deliberate: there's no degraded startup, since every
  request needs a working `jwks_uri` to validate a single token. Check the
  URL is reachable from inside the cluster (not just your laptop) and that
  `LOOM_IDP_CA_BUNDLE` is set if the IdP's CA isn't in the image's trust
  store.
- **Pod crashes on boot with "does not match the issuer published by its
  own OIDC discovery document"** -- a stored `auth.issuer` disagrees with
  what `auth.discovery_url` now reports (e.g. the IdP moved, or
  `LOOM_AUTH_ISSUER` was set to the wrong value). `issuer` is the trust
  anchor every token's `iss` claim is checked against, so this refuses to
  start rather than picking a side. Fix it with `loom config set
  auth.issuer <value>` (rerun the container after), or clear it (`loom
  config set auth.issuer ''`) to let it re-derive from discovery on next
  startup -- and stop pinning `LOOM_AUTH_ISSUER` explicitly if you don't
  need to.
- **HPA shows `<unknown>` for CPU** -- metrics-server isn't installed, or
  the container has no `resources.requests.cpu` set (both Deployments here
  do, by default).
- **One user gets `401 Not authorized`** -- the token itself is the
  problem: missing, expired, or otherwise fails to decode. `loom auth
  login` again is the fix.
- **One user gets `403`, even right after `loom auth login` succeeds** --
  the token is fine; either `get_current_principal` couldn't resolve its
  `sub` to a `Principal` in the requested Tenant, or the token's
  scopes/roles don't cover the action (`InsufficientScopeError`). For the
  Principal case, two possibilities: no `Principal` row exists yet for
  that `sub` in that Tenant at all -- run `loom principal create
  --tenant-id <their tenant> --external-id <their sub>` as the platform
  administrator to provision one; or it exists in a *different* Tenant
  than the request names -- legitimate for an identity provisioned in more
  than one Tenant on purpose (e.g. a consultant), fixed by `loom auth
  set-tenant <tenant_id>` rather than a new Principal. For the scope case,
  the identity's assigned role doesn't grant the attempted action (see
  "Authorization model" below) -- an IdP-side role change, not something
  `loom principal create` fixes. Re-running `loom auth login` does not fix
  any of these three -- the token it gets back will look identical.

## Authorization model

Access is governed by OAuth2 scopes of the form
`catalog:{resource}:{action}`, bundled into five composite roles your IdP
assigns. `src/loom/idp/catalog_roles.py` is the single source of truth;
`loom idp register` declares all of it under the RESTful API client. See
[docs/architecture.md](architecture.md#authorization-scopes-and-roles) for
how a token's scopes are resolved and checked at request time.

| Role | Grants |
|---|---|
| `catalog-viewer` | Read Capability/Agent/Skill/Tool/DataSource/DataProduct/ModelEndpoint |
| `catalog-editor` | ...plus create/update (write) them |
| `catalog-approver` | Read, plus lifecycle transitions (approve/publish/deprecate/retire) |
| `catalog-admin` | Read, write, and transition -- full content-tier access |
| `catalog-platform-admin` | Everything `catalog-admin` has, plus read/write on Tenant, Principal, and Environment |

Only `catalog-platform-admin` can create Tenants or provision Principals;
every other role operates strictly within whichever Tenant a Principal has
already been provisioned into.

### Assigning a role

Roles and permissions are assigned entirely in the IdP -- there is no
`loom` command for this. In Keycloak: **Users** → select or **Add user** →
**Role mapping** → **Assign role** → **Filter by clients** → find the role
(e.g. `catalog-viewer`) under your API client (`loom-catalog-api` by
default) → **Assign**. Nothing else to configure on the account -- no IdP
attribute carries a Tenant; that comes entirely from the `Principal` row
(see [docs/architecture.md](architecture.md#multi-tenancy-and-principal-resolution)).

Holding a role is necessary but not sufficient for content-tier access: a
`Principal` row must also exist for that identity in the specific Tenant
they'll work in, or every request 403s regardless of scope (see
Troubleshooting above). Provision one:

```
loom principal create --tenant-id <id> --kind {user,agent,service_account} \
  --external-id <their token's sub claim>
```

### Bootstrapping the first Tenant and Principal

`loom db upgrade` already creates one Tenant (slug `default`) on an empty
database, so a single-Tenant deployment needs nothing beyond the platform
administrator's own login:

1. Grant one human's IdP account the `catalog-platform-admin` role (see
   "Assigning a role" above) -- this requires `loom idp register` to have
   already run, since that's what creates the role.
2. `loom auth login` as that person. `POST /tenants/{tenant_id}/principals`
   only requires the `catalog:principal:write` scope
   `catalog-platform-admin` grants -- not an already-provisioned
   `Principal` -- so the CLI auto-registers one for this identity in the
   `default` Tenant on first login (best-effort; `loom principal create`
   below remains available if it can't reach the API).
3. Only if `default` isn't enough:

   ```
   loom tenant create acme "Acme Corp"
   loom principal create --tenant-id <id from `loom tenant list`> --kind user \
     --external-id <sub from `loom auth whoami`>
   ```

Every subsequent Tenant/Principal goes through the same two commands --
there's no separate bootstrap-only code path, so it's always
policy-checked normally and audited (see
[docs/architecture.md](architecture.md#audit-trail)).

## Reference

### Configuration

Every container reads its configuration from a YAML file at
`$LOOM_CONFIG_PATH`. `docker/entrypoint.sh` builds that file from plain
environment variables at container start, then execs the real command --
so in practice you configure the container via environment variables, not
by hand-editing YAML.

| Environment variable | Config key | Required |
|---|---|---|
| `LOOM_CONFIG_PATH` | *(where the file is written)* | yes for Kubernetes (must point inside the writable `config` `emptyDir` mount); optional for Compose/plain `docker run` |
| `LOOM_DB_HOST` | `database.host` | yes |
| `LOOM_DB_PORT` | `database.port` | no (default `5432`) |
| `LOOM_DB_NAME` | `database.database` | no (default `loom`) |
| `LOOM_DB_USERNAME` | `database.username` | no (default `loom`) |
| `LOOM_DB_PASSWORD` | `database.password` | yes -- entrypoint.sh refuses to start without it (or `LOOM_ALLOW_NO_DB_PASSWORD=1` for trust-auth Postgres) |
| `LOOM_AUTH_DISCOVERY_URL` | `auth.discovery_url` | yes -- the primary, stored value; startup fetches it and fails immediately if it doesn't respond |
| `LOOM_AUTH_AUDIENCE` | `auth.audience` | yes |
| `LOOM_AUTH_ISSUER` | `auth.issuer` | no -- auto-populated from `auth.discovery_url`'s own `issuer` field on first startup and persisted from then on; setting it explicitly only matters to pin a *specific* value startup then verifies against (see "Issuer mismatch" below) |
| `LOOM_AUTH_MCP_AUDIENCE` | `auth.mcp_audience` | yes for catalog-mcp -- it validates tokens against this, not `auth.audience` |
| `LOOM_AUTH_SWAGGER_CLIENT_ID` | `auth.swagger_client_id` | no -- only if exposing `/docs` (catalog-api only) |
| `LOOM_AUTH_CLI_CLIENT_ID` | `auth.cli_client_id` | no -- unused by the servers themselves |
| `LOOM_CATALOG_API_BASE_URL` | `catalog.api_base_url` | no -- only matters running `loom` CLI commands inside the container |
| `LOOM_IDP_CA_BUNDLE` | *(read directly by `loom.tls.build_ssl_context`)* | only if your IdP's CA isn't in the image's trust store |

**Why a writable path, not a mounted Secret file, for config**: `loom
config set` round-trips through `RootConfig.save()`, which
`chmod(0o600)`s the file it just wrote on every `loom` invocation, not
just `config set` -- so `$LOOM_CONFIG_PATH` can't point at a
read-only-mounted Secret. Instead, secrets arrive as plain environment
variables and entrypoint.sh writes them into a path the container itself
owns (a small `emptyDir`, so this still works under
`readOnlyRootFilesystem: true`).

**Hand-authoring a config YAML directly**: `RootConfig.load()` requires the
file to contain a `config_path` key matching its own path -- a file
written by `loom config set`/entrypoint.sh always has one; a hand-authored
file without it fails validation. Generate it with `loom config set` once
and reuse that file instead.

### `loom idp register` reference

```
loom idp register --issuer-url https://idp.example/realms/loom \
  --client-id loom-catalog-api --api-base-url https://api.example.com
```

Prompts for the Keycloak admin username/password (the standard superadmin
realm is `master`). To avoid the prompt, set
`LOOM_IDP_ADMIN_USERNAME`/`LOOM_IDP_ADMIN_PASSWORD`, or pass
`--admin-username`/`--admin-password` (flags > env vars > prompt). If the
admin account lives in a different realm or client, pass
`--admin-realm`/`--admin-client-id`.

Registers all four clients described in
[docs/architecture.md](architecture.md#oauth-client-topology) in one run.
Each confidential client's generated secret is printed once -- store it
securely. Every step is idempotent against Keycloak (a 409 on an
already-registered client is treated as success), so a run that fails
partway through is safe to re-run in full. Assigning roles to actual
users/service accounts is a separate step (see "Authorization model"
above).

Each client's `--*-client-id` is what config/tokens reference; its
human-readable Keycloak "Name" is separate (`Loom :: REST`, `Loom
:: MCP`, `Loom :: Swagger UI`, `Loom :: CLI` by default) -- override with
`--client-name`/`--mcp-client-name`/`--swagger-client-name`/
`--cli-client-name`. `--mcp-client-id`/`--swagger-client-id`/
`--cli-client-id` default to `{client-id}-mcp`/`-swagger`/`-cli`.

**Session length**: how long a CLI login lasts is Keycloak's realm/client
"Access Token Lifespan" (commonly a few minutes by default); `loom`
doesn't lengthen it -- the cached `refresh_token` is stored but not yet
used to silently renew, so a lapsed session means a full `loom auth login`
again. Override it for just the CLI client at registration time (safe to
re-run against an already-registered client to change later):

```
loom idp register --issuer-url https://idp.example/realms/loom \
  --client-id loom-catalog-api --api-base-url https://api.example.com \
  --access-token-lifespan 1800
```

(seconds; also settable via `LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN`, flag
takes precedence.)

### `loom idp unregister` reference

The inverse of `register`: deletes the four clients it created and clears
the `auth.*` fields it set (`issuer`, `audience`, `discovery_url`,
`mcp_audience`, `swagger_client_id`, `cli_client_id`).

```
loom idp unregister --issuer-url https://idp.example/realms/loom
```

Takes the same admin-login flags as `register`
(`--admin-username`/`--admin-password`/`--admin-realm`/`--admin-client-id`).
No `--client-id` is required if `config.auth.audience` is already set (the
common case -- you're unregistering what a prior `register` on this same
config just created); pass it explicitly to unregister a different
environment's clients than the ones currently configured locally.
`--mcp-client-id`/`--swagger-client-id`/`--cli-client-id` likewise default
to whatever's stored in `config.auth`, falling back to the
`{client-id}-mcp`/`-swagger`/`-cli` convention only if nothing is stored.

Every deletion is idempotent (a client already gone, or never registered,
is reported and skipped, not an error), so this is safe to re-run. Does
**not** clear a cached `loom auth login` session (`config.auth.session`)
-- run `loom auth logout` separately if the identity that login belongs to
has nowhere left to authenticate against.

### `loom db` reference

```
loom db upgrade [<revision>]     # apply migrations; head if omitted
loom db downgrade <revision>
loom db current
loom db history
loom db revision -m "message" [--autogenerate]
```

`upgrade` on an empty database also creates one Tenant (slug `default`) --
a no-op, safe to re-run, once any Tenant exists.

### `loom tenant` / `loom principal` reference

Neither is a `VersionedEntity` -- no `lifecycle_state`/version history, no
`versions`/`transition`. Tenant's `update` is a real in-place `PATCH`;
Principal has no `update` at all -- its human-readable name comes live from
the IdP's own `name` claim, not a stored field. Neither has `delete`.

```
loom tenant create <slug> <name>
loom tenant list [--limit N] [--offset N]
loom tenant show <tenant_id>
loom tenant update <tenant_id> <name>

loom principal create --tenant-id <id> --kind {user,agent,service_account} \
  --external-id <sub>
loom principal list [--tenant-id <id>] [--limit N] [--offset N]
loom principal show <principal_id>
```

`--external-id` is the identity a token has to carry (its `sub` claim) for
`get_current_principal` to match it up at request time. `--tenant-id` is
always required on `principal create` -- no token carries one to default
from. `principal list` defaults it to the locally-selected Tenant
(`loom auth set-tenant`) instead.

### Live IdP integration tests

`tests/integration/` runs the Swagger/CLI login flows and a claims round
trip against a real Keycloak instance instead of a mocked transport, and
is excluded from the default `pytest` run (self-skips without live
credentials):

```
export LOOM_IDP_ISSUER_URL=https://idp.example/realms/loom
export LOOM_IDP_ISSUER_ADMIN_USERNAME=admin
export LOOM_IDP_ISSUER_ADMIN_PASSWORD=<secret>
export LOOM_IDP_CA_BUNDLE=/path/to/ca-bundle.pem   # only if needed, see above

pytest tests/integration/ -m live_idp
```

Three tiers, gated independently:

- **`test_discovery.py`** needs only `LOOM_IDP_ISSUER_URL` -- confirms the
  issuer is reachable and its discovery document advertises what
  `security.py` needs plus the device code grant.
- **`test_swagger_login.py`** / **`test_cli_device_flow.py`** need admin
  credentials too -- register throwaway `loom-it-*` clients and confirm
  their Keycloak-side config lines up with the live discovery document;
  the device-flow test drives one real `start()`/`poll()` round trip.
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
can hide. Also self-skips without live credentials:

```
export LOOM_DB_HOST=localhost
# LOOM_DB_PORT/LOOM_DB_NAME/LOOM_DB_USERNAME/LOOM_DB_PASSWORD default
# the same way they do for `loom db upgrade` -- see "Configuration" above.

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
