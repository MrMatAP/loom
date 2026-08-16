# Loom Catalog (VS Code extension)

Authenticate against a Loom Catalog API and browse/create `Capability`,
`Agent`, and `ModelEndpoint` entities from the sidebar, without leaving the
editor. See `/CLAUDE.md` at the repo root for the full entity model this
mirrors.

This is a thin client, in the sense `CLAUDE.md`'s "Client — IDE plugin"
section means it: it holds no authoritative data beyond the session cache,
and it talks to the same `/api/v1/tenants/{tenant_id}/...` REST surface the
`loom` CLI does (`src/loom/catalog_client.py`), not a separate backend.

## Sidebar

The "Loom" activity bar icon opens one tree view, **Catalog**, with one
category per entity kind (Capabilities, Agents, Models). Expand a category
to list its current versions. Adding a new entity kind later (Skill, Tool,
DataSource, ...) is a one-line addition to `src/registry.ts`, not a new
view.

Clicking a Capability or Model Endpoint shows its full JSON in an editor
tab (also reachable for any entity via its right-click menu, "Loom: Show
Details"). Clicking an **Agent** instead opens its system prompt directly
in an editable buffer -- see "Editing an Agent's prompt" below.

Use the `+` inline action on a category (or `Loom: Create...` from the
command palette) to create a new entity of that kind.

## Editing an Agent's prompt

`Agent.prompt` is the versioned system prompt (CLAUDE.md's entity table).
Clicking an Agent in the sidebar opens it as a plain-text editor tab,
seeded with its current version's prompt. Edit it like any file and hit
Ctrl/Cmd+S: the save posts a new Agent version (`POST /agents/{entity_id}/
versions`) carrying every other field -- layer, model binding, memory
scope, `llm_config`, `permission_boundary` -- forward unchanged from the
version the buffer was opened from. This registry is append-only/versioned
by design (see CLAUDE.md and `loom agent update`'s docstring), so each save
is a new version, not an in-place overwrite; the tab keeps editing the same
buffer, so repeated saves just keep bumping the version.

Creating a new Agent opens this same editor immediately afterward, seeded
with the create wizard's placeholder prompt -- the wizard itself only
collects enough to produce a usable Draft (see "Creating entities" below).

There's no conflict detection: a save always branches off whatever version
is current *on the server* at that moment, not whatever version the buffer
was opened from. Editing the same Agent from two places at once (two
buffers, or this extension and `loom agent update`) is last-write-wins.

## Setup

1. `npm install`
2. `npm run compile` (or `npm run watch` while developing)
3. Press F5 in VS Code (with this folder open) to launch an Extension
   Development Host with the extension loaded.

Configure these settings before signing in (`Cmd+,` → search "loom"):

| Setting | Matches (in the `loom` CLI's `~/.loom`) | Notes |
|---|---|---|
| `loom.apiBaseUrl` | `catalog.api_base_url` | Default `http://localhost:8000` |
| `loom.discoveryUrl` | (none stored directly -- see below) | OIDC discovery document URL, e.g. `https://keycloak.example.com/realms/loom/.well-known/openid-configuration` |
| `loom.clientId` | `auth.cli_client_id` | Reuse the CLI's device-flow client (run `loom idp register` once if you haven't), or register a dedicated public client |
| `loom.caBundlePath` | (maps to `LOOM_IDP_CA_BUNDLE`) | Only needed if your IdP's CA isn't in Node's built-in trust store -- see below |

If you've already run `loom auth login` from this machine, `loom.apiBaseUrl`
and `loom.clientId` are sitting in `~/.loom` (`catalog.api_base_url` and
`auth.cli_client_id`) -- copy them across. `loom.discoveryUrl` isn't stored
there directly: append `/.well-known/openid-configuration` to `~/.loom`'s
`auth.issuer`.

**Never set `loom.discoveryUrl` to a bare issuer URL** -- the extension
never assumes an issuer's endpoints live at Keycloak's conventional
`/protocol/openid-connect/...` paths; it fetches this document once and
reads `issuer`, `token_endpoint`, `device_authorization_endpoint`, etc. out
of it (see `src/discovery.ts`), so it keeps working against any
OIDC-compliant IdP, not just Keycloak.

## Signing in

Run **Loom: Sign In** (command palette or the welcome view's button). This
performs an OAuth2 Device Authorization Grant -- the same flow `loom auth
login` uses -- against the `device_authorization_endpoint` and
`token_endpoint` read from `loom.discoveryUrl`'s document, and opens your
browser with the user code pre-filled. Tokens are cached in VS Code's
`SecretStorage`, never in settings or `globalState`. An access token
nearing expiry is refreshed silently using the cached refresh token; if
that fails too, you're prompted to sign in again.

After signing in, the extension lists Tenants available to your identity
(`GET /tenants/mine`) and either auto-selects the only one or prompts you
to pick. Every resource but Tenant itself is nested under
`/tenants/{tenant_id}/...`, so nothing else in the sidebar works until a
Tenant is selected -- the status bar item on the bottom left always shows
which one is active, and clicking it re-opens the picker.

**Not ported from the CLI on purpose:** `loom auth login`'s first-run
convenience of auto-registering a platform administrator's own Principal in
the `default` Tenant. Silently issuing a `POST /principals` write on sign-in
would be a surprising side effect of what looks like a read-only UI action.
If your very first login (via any client) is as a platform admin, you'll
see Tenants listed but every create/list call 403s -- the error message
gives you the exact `loom principal create --tenant-id <id> --kind user
--external-id <sub>` command to run once, from a terminal, to fix that.

## Creating entities

Each entity kind's create flow only asks for what its `*CreateRequest`
schema actually requires server-side (see `src/loom/api/catalog/<kind>/
schemas.py`) -- `tenant_id`/`owner_id`/`created_by_id`/`lifecycle_state`
are never client inputs; the server derives the first three from your
Principal and gates the last behind the lifecycle state machine, so the
extension doesn't offer to set any of them directly.

- **Capability**: name, description. `target_metrics` starts empty --
  add them via `loom capability create --target-metrics` or a version
  update for now.
- **Model Endpoint**: name, protocol, model, optional base URL/description.
  `auth_binding_id` (credential vault binding) isn't set here; wire it up
  via the CLI if the endpoint needs one.
- **Agent**: name, layer, memory scope, prompt, and an optional model
  binding picked from your Tenant's current Model Endpoints. The picker
  submits the endpoint's `entity_id`, not its row `id` -- `Agent.
  model_binding_id` is a *floating* reference that always resolves to
  whichever version is currently `is_current` (see CLAUDE.md). `llm_config`
  and `permission_boundary` are sent as `{}`; refine them via `loom agent
  update` once the Draft exists.

## TLS

Outbound calls use Node's built-in `https` module rather than global
`fetch`, specifically so `loom.caBundlePath` can take effect: Node's
`fetch` (undici) fixes its TLS trust decisions when the extension host
process starts, so a setting picked afterwards can't influence it. If
`loom auth login` needed `LOOM_IDP_CA_BUNDLE` to reach your IdP (see
`src/loom/tls.py`), point `loom.caBundlePath` at the same PEM file.

## Status

Compiles clean (`npm run typecheck`) and bundles clean (`npm run
compile`), but has not been run inside an actual Extension Development
Host yet -- do that via the F5 step above before relying on it.
