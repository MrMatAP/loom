# Split the CLI, config and IdP roles by trust domain

**Context**: A single `loom` command mixed infrastructure commands (`db upgrade`, `idp register`) with user commands (`auth login`, entity CRUD), and all of them read one `RootConfig` YAML file (`~/.loom`). That file held the database password in cleartext, the IdP settings the API server loaded at startup, and the user's cached session tokens. A user who only wanted to log in and create a Capability had DB credentials on disk, and `loom auth login` only worked after someone had run `loom idp register` against the same file. The IdP's single `catalog-platform-admin` role also bundled "can bootstrap Tenants and Principals" with every content scope, under a name that suggested infrastructure administration.

**Decision**: Split by who holds which credentials (see `CONTEXT.md`: Platform Administrator, Platform Operator, Platform User).

- Two binaries. `loom` (Platform Operators and Users) talks exclusively to the authenticated API and needs only an IdP login. `loom-admin` (Platform Administrator) owns `db` (migrations, default-Tenant seed), `idp` (register/unregister), `operator` (grant/list), `api`/`mcp` provisioning and its own `config`. `db` and `idp` are removed from `loom` without a deprecation shim.
- `RootConfig` is replaced by unrelated models: `UserConfig` (`~/.config/loom/config.yaml`), `AdminConfig` (`~/.config/loom/admin.yaml`) and the server configs, `ApiServerConfig` and `McpServerConfig` (one file per server, for container/Kubernetes deployment, emitted by `loom-admin idp register`). Each has its own path env var. The old `~/.loom` is ignored, with no migration.
- `loom auth login --server <url>` bootstraps from a public `GET /.well-known/loom-client` returning `discovery_url`, `cli_client_id` and `api_base_url`. `cli_client_id` defaults to `loom-cli`. A differing explicit local value produces a warning, and the local value wins.
- IdP roles are flat, with no IdP groups: `catalog-viewer`, `catalog-editor`, `catalog-approver`, `catalog-maintainer` (the union of the three, formerly `catalog-admin`) and `platform-operator` (formerly `catalog-platform-admin`, now platform scopes only). The platform scopes are renamed `catalog:{tenant,principal,environment}:*` to `platform:...:*`. `loom-admin operator grant <username>` (which also creates their Principal) assigns `platform-operator` and `catalog-maintainer` to an existing IdP account. Dropping `catalog-maintainer` removes an Operator's content access.
- The first-login auto-registration of a Principal is removed from `loom auth login`. Principal creation is an explicit Platform Operator action.

**Why**: The point is that the credentials a person needs determine what their tool can reach, rather than relying on convention. A separate file for each trust domain means `loom` can run on a laptop with no DB or IdP admin secrets. Keeping roles flat (no groups) means the server's role-name-to-scope expansion stays unchanged and there is one fewer IdP concept to configure.

## Considered Options

- **One config file, per-CLI validation of its own section** — rejected: `loom` would still have read access to the DB password on disk.
- **Admin settings from environment variables only** — rejected as the only path (secrets still overridable by env), because the file form is more practical for repeated admin work.
- **Deprecation shims for `loom db`/`loom idp`** — rejected: the project is experimental, and a shim would keep `loom` loading DB config.
- **IdP groups wrapping the roles** — rejected: roles and scopes are sufficient; the Operator's two roles are granted directly.
- **A single `RootConfig` retained for the server** — rejected: a model named "Root" with nothing above it, and the server's runtime DB credentials need to diverge from migration credentials anyway.

## Consequences

- Breaking: role names, scope names (`catalog:{tenant,principal,environment}:*` → `platform:*:*`), config paths and command locations all change. Existing IdP deployments must re-run `loom-admin idp register`, and existing tokens go stale.
- `catalog-maintainer` is a content role. It is not specific to Platform Operators, and the name avoids "admin"/"operator" so it can't be confused with the Platform roles.
- The server's DB credentials are separate from `AdminConfig`'s migration credentials. `loom-admin api|mcp config` creates the dedicated DML-only roles (see docs/admin-guide.md).
- `loom-admin` shares the Keycloak client in `loom.idp`. Keycloak admin credentials are never persisted (flags, then `LOOM_IDP_ADMIN_*`, then a prompt).
