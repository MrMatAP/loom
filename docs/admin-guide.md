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

# 2. Migrate the database (creates a `default` Tenant on an empty DB).
#    `loom-admin` reads ~/.config/loom/admin.yaml (or $LOOM_ADMIN_CONFIG_PATH);
#    the plain `loom` CLI never needs these settings.
loom-admin config set database.host db.example.com
loom-admin config set database.username loom
loom-admin config set database.password <secret>
loom-admin db upgrade

# 3. Register the four OAuth clients with your IdP
loom-admin idp register --discovery-url https://idp.example/realms/loom/.well-known/openid-configuration \
  --client-id loom-catalog-api --api-base-url https://api.example.com

# 4. Deploy (pick one)
docker compose up --build                    # local / trial
kubectl apply -k deploy/kubernetes            # production, see "Operate" below

# 5. Make one existing IdP account the first Platform Operator (also creates
#    their Principal in the default Tenant)
loom-admin operator grant alice

# 6. As that person: log in
loom auth login --server https://api.example.com
```

See "Operate" and "Reference" below for the detail behind each step.

## Operate

### Running locally with Docker Compose

`docker-compose.yaml` at the repo root runs Postgres, a one-shot `loom-admin db
upgrade` migration, and single instances of both services -- useful for
trying the Catalog out on a single machine, not a horizontal-scaling
story (that's Kubernetes below).

```
cp .env.example .env   # fill in LOOM_DB_PASSWORD, LOOM_AUTH_DISCOVERY_URL, LOOM_AUTH_AUDIENCE
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
   `LOOM_AUTH_DISCOVERY_URL`.

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

   `loom-db-migrate` runs `loom-admin db upgrade` exactly once, as a dedicated
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

### Platform Administrator, Platform Operator, Platform User

Three kinds of people touch a deployment (see `CONTEXT.md`):

- The **Platform Administrator** sets up the database and the IdP with
  `loom-admin`. They hold infrastructure credentials (database DSN, IdP
  admin login), not catalog roles.
- The **Platform Operator** manages Tenants and Principals through the
  authenticated API with `loom`. Every other identity the Catalog knows
  about -- a Tenant's own users, agents, service accounts -- is a
  `Principal` row scoped to one Tenant; the Operator is the exception: an
  IdP account holding `platform-operator` and no Tenant scope, which
  onboards the very first Tenant, so it can't itself depend on one existing.
- A **Platform User** holds one or more catalog content roles
  (`catalog-viewer`, `-editor`, `-approver`, `-maintainer`) and works
  inside a Tenant.

`loom` needs none of the Administrator's settings: it only needs the
server's URL (`loom auth login --server`) and an IdP login. See "Authorization
model" below for the roles and the one-time bootstrap sequence.

### TLS trust for the IdP connection

Every outbound call to the IdP (`loom-admin idp ...`, `loom auth login`, JWKS
resolution, OIDC discovery) trusts the OS-native certificate store by
default. If your IdP's certificate is
issued by a CA that's trusted system-wide but isn't visible to that
OS-trust-store lookup on your platform (observed on macOS with a CA
installed via a management tool into the login keychain rather than the
System roots), set an explicit override instead of fighting the OS store:

```
export LOOM_IDP_CA_BUNDLE=/path/to/ca-bundle.pem
loom-admin idp register ...
```

Inside a container this affects JWKS resolution at request-validation
time too, not just CLI commands -- mount the CA bundle (a ConfigMap volume
works well) and set `LOOM_IDP_CA_BUNDLE` to its path.

### Verifying interactively

The one step in the Swagger UI and CLI login flows that can't be checked
without a browser is a human approving the login. To confirm that step
works end to end at least once after registering clients on a new IdP
instance, do it by hand -- open `/docs`, click Authorize, log in; then run
`loom auth login` and approve the printed link. If either fails, the
registration command's own output
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
- **HPA shows `<unknown>` for CPU** -- metrics-server isn't installed, or
  the container has no `resources.requests.cpu` set (both Deployments here
  do, by default).
- **One user gets `401 Not authorized`** -- the token itself is the
  problem: missing, expired, or otherwise fails to decode. `loom auth
  login` again is the fix.
- **One user gets `403`, even right after `loom auth login` succeeds** --
  the token is fine; either it doesn't resolve to a `Principal` in the
  requested Tenant, or the identity's scopes/roles don't cover the action.
  For the Principal case, two possibilities: no `Principal` row exists yet
  for that `sub` in that Tenant at all -- run `loom principal create
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
`catalog:{resource}:{action}` (content) and `platform:{resource}:{action}`
(tenancy and identity), bundled into five composite roles your IdP
assigns. `loom-admin idp register` declares all of it under the RESTful API
client. See
[docs/architecture.md](architecture.md#authorization-scopes-and-roles) for
the vocabulary's single source of truth and how a token's scopes are
resolved and checked at request time.

| Role | Grants |
|---|---|
| `catalog-viewer` | Read Capability/Agent/Skill/Tool/DataSource/DataProduct/ModelEndpoint |
| `catalog-editor` | ...plus create/update (write) them |
| `catalog-approver` | Read, plus lifecycle transitions (approve/publish/deprecate/retire) |
| `catalog-maintainer` | Read, write, and transition -- full content-tier access |
| `platform-operator` | Read/write on Tenant, Principal, and Environment (platform scopes only; grant `catalog-maintainer` as well for content access) |

Only `platform-operator` can create Tenants or provision Principals;
every other role operates strictly within whichever Tenant a Principal has
already been provisioned into.

### Assigning a role

Content roles and permissions are assigned in the IdP. (The one exception
is `loom-admin operator grant <username>`, which assigns the Platform
Operator pair -- `platform-operator` and `catalog-maintainer` -- to an
existing account.) In Keycloak: **Users** → select or **Add user** →
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

`loom-admin db upgrade` already creates one Tenant (slug `default`) on an empty
database, so a single-Tenant deployment needs nothing beyond the first
Operator's own Principal:

1. As the Platform Administrator, run `loom-admin operator grant
   <username>` for an existing IdP account (this requires `loom-admin idp
   register` to have already run, since that's what creates the roles).
   It assigns `platform-operator` and `catalog-maintainer` in the IdP and
   creates that person's Principal in the `default` Tenant (`--tenant
   <slug-or-id>` picks another one). It's idempotent, and writes the same
   `principal.create` audit event the API would, attributed to
   `loom-admin`. To take away that person's content access later, remove
   `catalog-maintainer` from them in the IdP. `loom-admin operator list`
   shows every Operator with the Tenants they have a Principal in.
2. As that person, `loom auth login --server <api-url>`. Nothing else is
   needed -- their Principal already exists. Login deliberately doesn't
   create Principals for anyone else: that stays an Operator action
   (`loom principal create`), not a side effect of logging in.
3. Only if `default` isn't enough:

   ```
   loom tenant create acme "Acme Corp"
   loom principal create --tenant-id <id from `loom tenant list`> --kind user \
     --external-id <sub from `loom auth whoami`>
   ```

Every subsequent Tenant/Principal goes through the same two commands, so
it's policy-checked normally and audited. The one exception is `loom-admin
operator grant`, which writes the Operator's Principal straight to the
database (the Platform Administrator has no API login) -- it still writes
the audit event (see
[docs/architecture.md](architecture.md#audit-trail)).

## Reference

### Configuration

Three separate files, one per trust domain (see
[ADR 0002](adr/0002-split-cli-and-config-by-trust-domain.md)):

| File | Read by | Holds | Path (override) |
|---|---|---|---|
| user config | `loom` | API URL, cached IdP discovery URL, CLI client id, login session | `~/.config/loom/config.yaml` (`LOOM_CONFIG_PATH`) |
| admin config | `loom-admin` | privileged database connection, record of what `idp register` created | `~/.config/loom/admin.yaml` (`LOOM_ADMIN_CONFIG_PATH`) |
| API config | `loom-catalog-api` | runtime database connection, IdP settings and the API/Swagger/CLI client ids, public API URL | `~/.config/loom/api.yaml` (`LOOM_API_CONFIG_PATH`) |
| MCP config | `loom-catalog-mcp` | runtime database connection, IdP discovery URL, the MCP client id (audience) | `~/.config/loom/mcp.yaml` (`LOOM_MCP_CONFIG_PATH`) |

After `idp register`, generate each server's file and its database role:

```
loom-admin api config --api-base-url https://api.example.com
loom-admin mcp config
```

`config` writes the file from what `idp register` recorded (`--output
<path>` overrides `$LOOM_API_CONFIG_PATH`/`$LOOM_MCP_CONFIG_PATH`).
By default `config` also creates the server's dedicated Postgres role
(`loom_api`/`loom_mcp`, or `--db-username`/`--db-user`; password generated
unless `--db-password`) and writes its credentials into the file. If you
pass **both** `--db-username` and `--db-password`, that role is assumed to
exist already: nothing is created and the credentials are written as-is.
`loom-admin api db-role` / `loom-admin mcp db-role` create just the role,
printing a generated password once; re-running rotates it.

**Prerequisite for automatic role creation:** the role `loom-admin`
connects as (`database.username` in the admin config) must have `LOGIN` and
`CREATEROLE`, e.g. `ALTER ROLE migrator CREATEROLE;` as a superuser.
Without `CREATEROLE`, role creation fails; create the roles yourself and
pass `--db-username`/`--db-password` instead.

The database stays owned by the `loom-admin` connection, which holds full
DDL rights because it runs the migrations. The server roles are DML-only:
`SELECT/INSERT/UPDATE/DELETE` on the tables and `USAGE/SELECT` on sequences
in `public`, no schema changes, no access to `alembic_version`. Default
privileges are set for tables the admin role creates later, so the grants
survive `loom-admin db upgrade` without being re-run. The old single `~/.loom`
file is ignored.

In containers, `docker/entrypoint.sh` builds the admin or server file from
plain environment variables at start, then execs the real command -- so in
practice you configure the container via environment variables, not by
hand-editing YAML.

| Environment variable | Config key | Required |
|---|---|---|
| `LOOM_API_CONFIG_PATH` / `LOOM_MCP_CONFIG_PATH` / `LOOM_ADMIN_CONFIG_PATH` | *(where the file is written)* | yes for Kubernetes (must point inside the writable `config` `emptyDir` mount); optional for Compose/plain `docker run` |
| `LOOM_DB_HOST` | `database.host` | yes |
| `LOOM_DB_PORT` | `database.port` | no (default `5432`) |
| `LOOM_DB_NAME` | `database.database` | no (default `loom`) |
| `LOOM_DB_USERNAME` | `database.username` | no (default `loom`) |
| `LOOM_DB_PASSWORD` | `database.password` | yes -- entrypoint.sh refuses to start without it (or `LOOM_ALLOW_NO_DB_PASSWORD=1` for trust-auth Postgres) |
| `LOOM_AUTH_DISCOVERY_URL` | `auth.discovery_url` | yes -- the single source of the IdP's issuer, signing algorithms and endpoints; startup fetches it and fails immediately if it doesn't respond |
| `LOOM_AUTH_AUDIENCE` | `auth.audience` | yes |
| `LOOM_AUTH_MCP_AUDIENCE` | `auth.audience` in the MCP config | yes for catalog-mcp -- its own client id, separate from `LOOM_AUTH_AUDIENCE` |
| `LOOM_AUTH_SWAGGER_CLIENT_ID` | `auth.swagger_client_id` | no -- only if exposing `/docs` (catalog-api only) |
| `LOOM_AUTH_CLI_CLIENT_ID` | `auth.cli_client_id` | no (default `loom-cli`) -- advertised to the CLI via `/.well-known/loom-client` |
| `LOOM_CATALOG_API_BASE_URL` | `api_base_url` | no -- advertised to clients via `/.well-known/loom-client`; derived from the request if unset |
| `LOOM_IDP_CA_BUNDLE` | *(read directly by `loom.tls.build_ssl_context`)* | only if your IdP's CA isn't in the image's trust store |

**Why a writable path, not a mounted Secret file, for config**: the CLIs
tighten the permissions on their config file on every invocation, not just
`config set` -- so the config path can't point at a read-only-mounted
Secret. Instead, secrets arrive as plain environment variables and the
container writes them into a path it owns itself (a small `emptyDir`, so
this still works under `readOnlyRootFilesystem: true`). See
[docs/architecture.md](architecture.md#configuration-and-secrets) for the
implementation detail behind this.

**Hand-authoring a config YAML directly** is fine -- the loader fills in
`config_path` itself -- but note the CLIs rewrite the file on every
invocation, so don't expect comments to survive.

### `loom-admin idp register` reference

```
loom-admin idp register --discovery-url https://idp.example/realms/loom/.well-known/openid-configuration \
  --client-id loom-catalog-api --api-base-url https://api.example.com
```

`--discovery-url` is only needed the first time: `auth.discovery_url` from
the config is used if set, otherwise the given value is stored there once
the document has been fetched. The issuer is read from that document.

Prompts for the Keycloak admin username/password (authenticated against the
`master` realm). To avoid the prompt, set
`LOOM_IDP_ADMIN_USERNAME`/`LOOM_IDP_ADMIN_PASSWORD`, or pass
`--admin-username`/`--admin-password` (flags > env vars > prompt). If the
admin account uses a different client, pass `--admin-client-id`.

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
`--cli-client-id` default to `{client-id}-mcp`/`-swagger` and `loom-cli`
(the CLI id is a fixed default so `loom` can find it without being told).

**Session length**: how long a CLI login lasts is Keycloak's realm/client
"Access Token Lifespan" (commonly a few minutes by default); `loom`
doesn't lengthen it -- the cached `refresh_token` is stored but not yet
used to silently renew, so a lapsed session means a full `loom auth login`
again. Override it for just the CLI client at registration time (safe to
re-run against an already-registered client to change later):

```
loom-admin idp register --discovery-url https://idp.example/realms/loom/.well-known/openid-configuration \
  --client-id loom-catalog-api --api-base-url https://api.example.com \
  --access-token-lifespan 1800
```

(seconds; also settable via `LOOM_IDP_CLI_ACCESS_TOKEN_LIFESPAN`, flag
takes precedence.)

### `loom-admin idp unregister` reference

The inverse of `register`: deletes the four clients it created and clears
the `auth.*` fields it set (`audience`, `discovery_url`,
`mcp_audience`, `swagger_client_id`, `cli_client_id`).

```
loom-admin idp unregister --discovery-url https://idp.example/realms/loom/.well-known/openid-configuration
```

Takes the same admin-login flags as `register`
(`--discovery-url`/`--admin-username`/`--admin-password`/`--admin-client-id`).
No `--client-id` is required if `config.auth.audience` is already set (the
common case -- you're unregistering what a prior `register` on this same
config just created); pass it explicitly to unregister a different
environment's clients than the ones currently configured locally.
`--mcp-client-id`/`--swagger-client-id`/`--cli-client-id` likewise default
to whatever's stored in `config.auth`, falling back to the
`{client-id}-mcp`/`-swagger` convention (and `loom-cli`) only if nothing is stored.

Every deletion is idempotent (a client already gone, or never registered,
is reported and skipped, not an error), so this is safe to re-run. Does
**not** clear a cached `loom auth login` session (`config.auth.session`)
-- run `loom auth logout` separately if the identity that login belongs to
has nowhere left to authenticate against.

### `loom-admin db` reference

```
loom-admin db upgrade [<revision>]     # apply migrations; head if omitted
loom-admin db downgrade <revision>
loom-admin db current
loom-admin db history
loom-admin db revision -m "message" [--autogenerate]
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
Loom to match it up at request time. `--tenant-id` is always required on
`principal create` -- no token carries one to default from. `principal
list` defaults it to the locally-selected Tenant (`loom auth set-tenant`)
instead.
