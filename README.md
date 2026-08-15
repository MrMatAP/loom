# Loom

An AI-Enabled Distributed Systems IDE.

- Domain model, invariants, and system design: [docs/architecture.md](docs/architecture.md)
- Installing and operating the Catalog API/MCP server: [docs/admin-guide.md](docs/admin-guide.md)
- Using an already-deployed Catalog: [docs/user-guide.md](docs/user-guide.md)

## How to build this

```
uv sync
uvx ruff check --output-format=github src   # lint
uv run pyrefly check                        # type check
uv build --wheel                            # produces dist/*.whl
```

For local development against the Catalog API without a container:

```
uvicorn loom.api.catalog.main:app --reload
```

Building the container image (needed for the Docker Compose/Kubernetes
paths in [docs/admin-guide.md](docs/admin-guide.md)):

```
docker build --build-arg VERSION=1.4.2 -t ghcr.io/your-org/loom:1.4.2 .
```

## How to test this

```
uv run pytest
```

Runs against an in-memory sqlite DB and a mocked IdP transport; fast, no
external dependencies. Three live suites are excluded by default
(self-skip without credentials/a running service) and exercise a real
Keycloak instance, a real Postgres instance, and a real OpenAI-compatible
LLM server respectively -- see
[docs/admin-guide.md](docs/admin-guide.md#live-idp-integration-tests),
[docs/admin-guide.md](docs/admin-guide.md#live-postgres-integration-tests),
and [docs/admin-guide.md](docs/admin-guide.md#live-llm-integration-test)
for the environment variables and `-m live_idp`/`-m live_db`/`-m live_llm`
invocations. Point the live-IdP suite at a disposable Keycloak realm, not
a shared one -- every object it creates is prefixed `loom-it-` and torn
down at the end of the run, but an interrupted run can leave one behind.
Point the live-Postgres suite at a disposable database, not production.
The live-LLM suite needs `uv sync --group live-llm` first (LangChain
isn't installed by default) and a local OpenAI-compatible
server (e.g. LM Studio) with a model loaded.

## How to release this

Fully automated via GitHub Actions (`.github/workflows/ci.yml`) -- there's
no manual release step. Every push runs lint, `uv build --wheel`, and the
full test suite. A push to `main` additionally versions the build
(`MrMatAP/mrmat-versioning-action`, semver from `major`/`minor` in the
workflow plus an auto-incrementing patch) and cuts a GitHub Release
(`MrMatAP/mrmat-release-action`) tagged with that version. The version is
threaded through as `MRMAT_VERSION`, read by `src/ci/__init__.py` and
exposed as `loom --version`/the package's own metadata; it is not
otherwise baked into behavior.

## Current limitations

- **No silent session refresh.** `loom auth login` caches a
  `refresh_token`, but nothing yet uses it to renew an expired session --
  a lapsed CLI session means a full `loom auth login` again, not a quiet
  refresh. See [docs/admin-guide.md](docs/admin-guide.md#loom-idp-register-reference)
  for extending the token lifespan instead.
- **The registry is append-only.** No resource supports delete; `update`
  always creates a new version rather than mutating in place.
- **CLI/MCP coverage is partial.** Only Capability, ModelEndpoint, and
  Agent are reachable from `loom` or the MCP server. Skill, Tool,
  DataSource, DataProduct, and Environment have REST routers but no CLI
  commands or MCP tools yet.
- **Evaluation, governance, and observability aren't exposed yet.**
  `EvalSuite`/`EvalRun`, `Policy`/`RoleBinding` (beyond the audit log),
  and `Trace`/`Metric` exist as SQLAlchemy models
  (`src/loom/model/evaluation.py`, `governance.py`, `observability.py`)
  with no REST, CLI, or MCP surface yet -- lifecycle transitions aren't
  actually gated by eval suites yet, despite that being the intended
  design (see [CLAUDE.md](CLAUDE.md)).
