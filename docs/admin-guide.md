# Catalog Admin Guide

Deploying, configuring, and operating the Catalog API and MCP server.
For what these services *do* and how to drive them once running, see
[README.md](../README.md) (REST API, CLI) and
[docs/user-guide.md](user-guide.md) (day-to-day usage). This guide covers
the parts specific to running them as containers: build, configuration,
Docker Compose, and Kubernetes.

## Architecture in one paragraph

Both services are stateless HTTP processes (FastAPI/Uvicorn) backed by a
shared PostgreSQL database and a shared external OIDC issuer -- neither
holds request-scoped state anywhere but the DB, and the MCP server runs
with `stateless_http=True` specifically so no session is pinned to one
process either (see `src/loom/api/catalog/mcp/main.py`). That's what makes
"horizontally scalable" true rather than aspirational: any request can go
to any replica, so scaling is purely a replica-count knob, not a
code-level concern.

## Prerequisites

- **PostgreSQL** (14+) reachable from wherever you run the containers.
  Provisioning it is out of scope here -- use whatever you already run
  (managed service, existing instance, operator-managed StatefulSet). None
  of the manifests below create one.
- **An OIDC issuer** with the Catalog's OAuth client already registered --
  see README.md's ["Registering the client with your
  IDP"](../README.md#registering-the-client-with-your-idp). Do this before
  deploying; the services fail closed (every request needs a bearer token)
  without it.
- **Docker** to build the image.
- For Kubernetes: **kubectl**, **kustomize** (or `kubectl apply -k`, which
  bundles an older kustomize), and a **metrics-server** installed in-cluster
  if you want the HorizontalPodAutoscalers to actually scale (they'll sit
  at `<unknown>`/minReplicas without one).

## Build

One image serves three roles -- the REST API, the MCP server, and the
`loom` CLI (used for `loom db upgrade` as a one-shot migration step) --
selected by which command runs, not by building three images. See the
comments in `/Dockerfile` for the full reasoning (reproducibility via
`uv.lock`, why one image, why a fixed non-root UID).

```
docker build --build-arg VERSION=1.4.2 -t ghcr.io/your-org/loom:1.4.2 .
docker push ghcr.io/your-org/loom:1.4.2
```

`VERSION` threads through to `loom --version` and the package metadata
inside the image (defaults to `0.0.0.dev0` if omitted) -- it does not
affect what gets installed, only how the built artifact identifies itself.

## Configuration

Every container reads its configuration from a YAML file (the same format
`loom config set` on your own machine produces) at `$LOOM_CONFIG_PATH`.
`docker/entrypoint.sh` builds that file from plain environment variables
at container start, then execs the real command -- so in practice you
configure the container via environment variables, not by hand-editing
YAML. The full mapping:

| Environment variable | Config key | Required |
|---|---|---|
| `LOOM_CONFIG_PATH` | *(where the file is written)* | yes for Kubernetes (must point inside the writable `config` `emptyDir` mount -- unset falls back to `$HOME/.loom`, which is on the read-only root layer there and fails the writability check below); optional for Compose/plain `docker run` |
| `LOOM_DB_HOST` | `database.host` | yes |
| `LOOM_DB_PORT` | `database.port` | no (default `5432`) |
| `LOOM_DB_NAME` | `database.database` | no (default `loom`) |
| `LOOM_DB_USERNAME` | `database.username` | no (default `loom`) |
| `LOOM_DB_PASSWORD` | `database.password` | yes -- entrypoint.sh refuses to start without it (see below) |
| `LOOM_AUTH_ISSUER` | `auth.issuer` | yes |
| `LOOM_AUTH_AUDIENCE` | `auth.audience` | yes |
| `LOOM_AUTH_DISCOVERY_URL` | `auth.discovery_url` | no -- derived from `auth.issuer` (`{issuer}/.well-known/openid-configuration`); `authorization_endpoint`/`token_endpoint`/`jwks_uri` are all read from that one document |
| `LOOM_AUTH_MCP_AUDIENCE` | `auth.mcp_audience` | yes for catalog-mcp -- it validates tokens against this audience, not `auth.audience` (separate resource-server client from the RESTful API, see `loom idp register`) |
| `LOOM_AUTH_SWAGGER_CLIENT_ID` | `auth.swagger_client_id` | no -- only if exposing `/docs` (catalog-api only) |
| `LOOM_AUTH_CLI_CLIENT_ID` | `auth.cli_client_id` | no -- unused by the servers themselves |
| `LOOM_CATALOG_API_BASE_URL` | `catalog.api_base_url` | no -- only matters if you run `loom` CLI commands *inside* the container |
| `LOOM_IDP_CA_BUNDLE` | *(not a config key -- read directly by `loom.tls.build_ssl_context`)* | only if your IDP's CA isn't in the image's trust store |

Two things entrypoint.sh checks before starting anything, both explained
further down: `LOOM_CONFIG_PATH`'s directory must be writable, and
`LOOM_DB_PASSWORD` must be set (or `LOOM_ALLOW_NO_DB_PASSWORD=1`
explicitly passed for a trust-auth Postgres).

**A note if you ever bypass entrypoint.sh** and hand-author a config YAML
directly (e.g. for a fully pre-baked config Secret): `RootConfig.load()`
requires the file to contain a `config_path` key matching its own path --
a file written by `loom config set`/entrypoint.sh always has one, but a
hand-authored file without it fails validation on load. Simplest fix:
generate it with `loom config set` once and reuse that file, rather than
writing the YAML by hand.

**Internal CA for your IDP**: if `LOOM_AUTH_ISSUER` isn't reachable over
TLS with a publicly-trusted certificate, mount the CA bundle into the
container (a ConfigMap volume works well) and set `LOOM_IDP_CA_BUNDLE` to
its path. This affects JWKS resolution at request-validation time inside
catalog-api/catalog-mcp themselves, not just CLI commands -- see
README.md's ["TLS trust for the IDP
connection"](../README.md#tls-trust-for-the-idp-connection).

### Why a writable path, not a mounted Secret file, for config

`loom config set` (which entrypoint.sh calls once per variable you've
set) round-trips through `RootConfig.save()`, which `chmod(0o600)`s the
file it just wrote -- every `loom` invocation does this, not just `config
set`, so there's no way to point `$LOOM_CONFIG_PATH` at a read-only-mounted
Secret and have it work. Instead, secrets arrive as plain environment
variables (`secretKeyRef` in Kubernetes, `.env` in Compose) and
entrypoint.sh writes them into a path the container itself owns. The
Kubernetes manifests back that path with a small `emptyDir` so this still
works under `readOnlyRootFilesystem: true` on the container's own root
layer.

### Fail-fast behavior

entrypoint.sh deliberately refuses to start rather than run with an
unusable configuration:

- **Unwritable `$LOOM_CONFIG_PATH` directory** -- exits with a specific
  message pointing at the missing writable mount, instead of the bare
  `[Errno 13] Permission denied` `loom config set` would otherwise print.
- **Unset/empty `LOOM_DB_PASSWORD`** -- exits rather than silently
  building a passwordless DSN. Without this check, a misnamed Secret key
  produces a pod that passes its readiness probe (`/healthz` never
  touches the database) and then 500s on every real request -- a much
  harder failure to diagnose than a crashlooping pod with a clear log
  line. Set `LOOM_ALLOW_NO_DB_PASSWORD=1` for a deliberately
  passwordless (trust-auth) Postgres.

## Running locally with Docker Compose

`docker-compose.yaml` at the repo root runs Postgres, a one-shot
`loom db upgrade` migration, and single instances of both services --
useful for trying the Catalog out or developing against it, not a
horizontal-scaling story (that's Kubernetes; every service here is a
single container).

```
cp .env.example .env   # fill in LOOM_DB_PASSWORD, LOOM_AUTH_ISSUER, LOOM_AUTH_AUDIENCE
docker compose up --build
```

The `migrate` service must exit 0 before `catalog-api`/`catalog-mcp`
start (`depends_on: condition: service_completed_successfully`) --
Compose enforces that ordering for you, unlike the Kubernetes path below.

## Deploying to Kubernetes

Manifests live under `deploy/kubernetes/`, composed via
[Kustomize](https://kustomize.io). Two files there are templates, not
applied resources -- `secret.example.yaml` and `ingress.example.yaml` --
see the comment at the top of each for why.

### 1. Point the manifests at your build

```
cd deploy/kubernetes
kustomize edit set image loom=ghcr.io/your-org/loom:1.4.2
```

### 2. Edit `configmap.yaml`

Every `CHANGEME` must be replaced -- in particular `LOOM_DB_HOST` (there is
no Postgres in this manifest set; point it at yours) and `LOOM_AUTH_ISSUER`.

### 3. Create the database credential Secret

```
kubectl create namespace loom-catalog
kubectl create secret generic loom-db-credentials \
  --namespace loom-catalog \
  --from-literal=password='<your Postgres password>'
```

(Or manage it through your own GitOps/sealed-secrets/external-secrets
pipeline -- `secret.example.yaml` is copy-paste starting material for
that; never commit a filled-in copy.)

### 4. Apply everything, then confirm the migration finished

```
kubectl apply -k .
kubectl wait --for=condition=complete job/loom-db-migrate \
  --namespace loom-catalog --timeout=120s
```

`loom-db-migrate` runs `loom db upgrade` exactly once. It's a dedicated
Job rather than a per-pod initContainer deliberately: running that command
concurrently from multiple starting replicas is the exact race
(`DuplicateObject` on a shared Postgres enum type, created twice against a
fresh database) this project's history already hit once -- see the Job's
own comments. `kubectl apply -k` gives no ordering guarantee between a Job
and a Deployment on its own, so the `kubectl wait` step above isn't
optional: the API/MCP Deployments' pods can and will start before the
schema is ready. That's not silently broken -- `/healthz` doesn't touch
the database, so those pods report Ready and simply return errors on real
requests until the migration completes, no restart needed once it does.
Verify with:

```
loom capability list   # against this deployment's catalog.api_base_url
# or, without a logged-in CLI session:
kubectl logs job/loom-db-migrate --namespace loom-catalog
```

### Rolling out a new version

Jobs are immutable (`spec.template` can't be patched), so a re-run after
a new image needs the old completed Job deleted first:

```
kustomize edit set image loom=ghcr.io/your-org/loom:1.5.0
kubectl delete job/loom-db-migrate --namespace loom-catalog --ignore-not-found
kubectl apply -k .
kubectl wait --for=condition=complete job/loom-db-migrate --namespace loom-catalog --timeout=120s
```

The Deployments themselves roll normally (`kubectl apply -k` triggers a
standard rolling update) -- no special handling needed there.

### Scaling

`catalog-api-hpa.yaml`/`catalog-mcp-hpa.yaml` target 70% average CPU
utilization between 2 and 10 replicas each; needs metrics-server (see
Prerequisites). To scale manually instead (or while metrics-server isn't
available):

```
kubectl scale deployment/loom-catalog-api --namespace loom-catalog --replicas=4
```

`pdb.yaml` keeps at least 1 replica of each up through voluntary
disruptions (node drains, cluster upgrades) once you're running more than
one -- the default.

### Exposing it externally

`ingress.example.yaml` is a starting template -- copy it, fill in
`ingressClassName`/host/TLS for your cluster. Whatever hostname you land
on for the REST API must match `LOOM_CATALOG_API_BASE_URL` in
`configmap.yaml`, and if you want Swagger UI's interactive login to work
through it, must also match the `--api-base-url` you registered via
`loom idp register` (see README.md) -- a mismatch there is the most common
reason that login redirect fails.

## Platform administrator

Every other identity the Catalog knows about -- a Tenant's own users,
agents, service accounts -- is a `Principal` row scoped to one Tenant. The
**platform administrator** is the one exception: an IDP account with no
Tenant scope at all, used to create and manage Tenants and Principals
across the whole deployment. It's what onboards the very first Tenant, so
it can't itself depend on one existing yet.

There's no `loom` command for this step by design -- granting
platform-wide access is a decision for whoever administers your IDP, made
once per admin, directly in the IDP:

1. Run `loom db upgrade` then `loom idp register` (see README.md) first --
   the second creates the `catalog-platform-admin` role under the
   `loom-catalog-api` client (or whatever `--client-id` you used) that the
   next step grants.
2. In Keycloak: **Users** → **Add user** → create an account for the
   person who will administer Tenants (existing users work too). Then
   **Role mapping** → **Assign role** → filter by **Filter by clients** →
   find `catalog-platform-admin` under your API client → **Assign**.
   Nothing else to configure on the account -- no IDP account attribute is
   involved anywhere in this flow (see "How a caller's Tenant is
   resolved" below).

`loom db upgrade` already created one Tenant for you -- slug `default` --
so a single-Tenant deployment needs nothing further here; skip straight to
`loom principal create` below with that Tenant's id (`loom tenant list`).
Create additional/differently-named Tenants only if you actually run more
than one. Unlike `POST /tenants`/`POST /principals` below, this one write
is not audited (`db upgrade` runs before any login, so there's no `sub` to
attribute it to) -- acceptable here since, unlike those two, the row
grants no identity access by itself; see `_seed_default_tenant` in
`src/loom/cli/db.py`:

    loom auth login
    loom tenant create acme "Acme Corp"          # only if `default` isn't enough
    loom principal create --tenant-id <id from above> --kind user \
      --external-id <sub from `loom auth whoami`>

`POST /tenants` and `POST /principals` only require the caller's token to
carry `catalog:tenant:write`/`catalog:principal:write` -- exactly what
`catalog-platform-admin` grants -- not an already-provisioned `Principal`,
which is what makes this work on a database that has no Tenants or
Principals in it yet. Both still write an `AuditEvent` for the action --
attributed to the caller's token `sub` (`details.actor_external_id`)
rather than a `Principal` row when, as here, there isn't one yet -- so the
bootstrap itself isn't a silent, unaudited path (see
`src/loom/api/catalog/audit.py`). See README.md's "Managing Tenants and
Principals from the CLI" for the full command reference.

Everything past this point -- Capability/Agent/Skill/Tool/DataSource/
DataProduct/ModelEndpoint access -- still goes through a Tenant-scoped
`Principal` the normal way (`resolve_principal` in
`src/loom/api/catalog/dependencies.py`), including for the platform
administrator themselves if they also want to act as one: `loom principal
create` for their own `sub` is all that's needed (see below).

### How a caller's Tenant is resolved

There's no `tenant_id` claim anywhere in this system -- not on a token,
not as an IDP account attribute. A caller's Tenant comes entirely from
`Principal.tenant_id`: `resolve_principal` looks up the one `Principal`
row whose `external_id` matches the token's `sub` and reads its
`tenant_id` off of that row. This is deliberate: it means onboarding a new
user is *one* action (`loom principal create`), not two (create the
Principal, *and* separately remember to tag their IDP account) -- the
second step was easy to forget and produced a confusing 401 with no
obvious cause (see Troubleshooting below).

One consequence: `external_id` is only unique *per Tenant*
(`uq_principal_tenant_external_id` in `src/loom/model/tenant.py`), so the
same `sub` can legitimately be provisioned in more than one Tenant -- a
consultant working across two customer Tenants, for instance. `loom auth
login` handles this proactively rather than waiting for a request to
401: right after obtaining tokens, it calls `GET /tenants/mine` (every
Tenant for a platform admin, i.e. anyone whose token carries
`catalog:tenant:read`; otherwise only the Tenants where the caller
already has a Principal) and:

- **Exactly one Tenant available** -- selected automatically, no prompt.
  This is what makes a fresh single-Tenant deployment work with zero
  extra steps: the `default` Tenant `db upgrade` created is the only
  choice, so it's just active.
- **More than one** -- the CLI lists them and requires picking one before
  login finishes.
- **None yet** -- login still succeeds (the tokens are valid), but prints
  a reminder to ask the platform administrator to run `loom principal
  create`.

The selection is stored locally (`config.auth.session.tenant_id`) and
sent as the `X-Loom-Tenant-Id` header on every subsequent request
(`loom.http_headers.TENANT_HINT_HEADER`); `resolve_principal` only
consults it to pick among *that identity's own* Principal rows, never to
grant access to a Tenant it isn't otherwise provisioned in -- an
incorrect or stale selection just 401s (see Troubleshooting below). Run
`loom auth set-tenant <tenant_id>` any time afterward to change it --
also how to resolve a login that couldn't reach the API to list Tenants
in the first place. With no arguments it shows the current selection;
`--clear` removes it; `loom auth logout` clears it too, and so does every
fresh `loom auth login` before it re-selects, since a new login may
resolve to a different identity. `loom auth whoami` shows the current
selection alongside the token's own claims.

A Principal also has no stored display name. A human-readable name comes
from the IDP's own `name` claim, read live off a caller's own token at
request time (`loom auth whoami`) -- not duplicated in the Catalog's
database, so it can't drift out of sync with the IDP.

## Troubleshooting

- **Pods `CreateContainerConfigError`** -- almost always a missing/misnamed
  Secret or key (`kubectl describe pod` names the exact key it couldn't
  find).
- **Pod stuck `CrashLoopBackOff` with entrypoint.sh's own log lines** (not
  a Python traceback) -- read them, they name the exact problem (unwritable
  config path, missing DB password); see "Fail-fast behavior" above.
- **Pods `Running`/`Ready` but every request 500s** -- migration hasn't
  completed yet, or completed against the wrong database. Check
  `kubectl get job/loom-db-migrate` and its logs first.
- **HPA shows `<unknown>` for CPU** -- metrics-server isn't installed, or
  the container has no `resources.requests.cpu` set (both Deployments here
  do, by default).
- **One user gets `401 Not authorized` on Capability/Agent/Skill/Tool/
  DataSource/DataProduct/ModelEndpoint requests, even right after `loom
  auth login` succeeds** -- this is *not* a missing scope (a missing scope
  is a `403`, not a `401`; see `require_scopes` in
  `src/loom/api/catalog/dependencies.py`). A `401` here means
  `resolve_principal` couldn't resolve the token's `sub` to exactly one
  `Principal`, for one of two reasons, in the order it checks them -- the
  CLI's error message names which one. (`/tenants`/`/principals` requests
  don't go through `resolve_principal` at all -- see "Platform
  administrator" above -- so this doesn't apply to `loom tenant`/`loom
  principal` commands.)
  1. **No `Principal` row exists yet** for that `sub` in the Catalog's own
     database -- run `loom principal create --tenant-id <their tenant> \
     --external-id <their sub>` as the platform administrator (see
     "Platform administrator" above) to provision one.
  2. **More than one `Principal` row matches that `sub`** (provisioned in
     more than one Tenant -- `external_id` is only unique per Tenant, see
     "How a caller's Tenant is resolved" above), **and no `X-Loom-Tenant-Id`
     header disambiguates it.** `loom auth login` should have already
     prompted for a selection in this case -- seeing this error usually
     means that prompt was skipped (a non-interactive login, or the
     tenant-listing call itself failed, both of which print their own
     warning at login time) or the local selection was since cleared. This
     is the expected, legitimate case for an identity provisioned in more
     than one Tenant on purpose (e.g. a consultant) -- not a bug to fix,
     just run `loom auth set-tenant <tenant_id>` to pick one. If it's *not*
     expected (the same person was provisioned twice by accident), that's
     a provisioning mistake to clean up instead. Either way the CLI's error
     message names it as ambiguous, not missing, so it's distinguishable
     from #1 above.
  Re-running `loom auth login` does not fix either of these -- the token
  it gets back will look identical.
