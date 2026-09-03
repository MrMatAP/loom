# Loom Catalog — Manual

Authenticate against a Loom Catalog API and browse, create, and edit
`Capability`, `Agent`, and `ModelEndpoint` entities without leaving the
editor.

This is a thin client: it holds no authoritative data beyond the session
cache and talks to the same `/api/v1/tenants/{tenant_id}/...` REST surface
the `loom` CLI does — not a separate backend.

> Open this manual any time with **Loom: Open Manual** (command palette, or
> the Catalog view's `...` menu).

---

## Setup

1. Open **Loom: Open Settings** (command palette, or the `...` menu) — it
   filters Settings to the `loom.` keys.
2. Fill in at least `loom.discoveryUrl` and `loom.clientId`. If you've run
   `loom auth login` on this machine, `loom.apiBaseUrl` and `loom.clientId`
   are already in `~/.loom` as `catalog.api_base_url` and
   `auth.cli_client_id` — copy them across.

| Setting | What it is |
|---|---|
| `loom.apiBaseUrl` | Base URL the Catalog API is served from. Default `http://localhost:8000`. |
| `loom.discoveryUrl` | Your IdP realm's **OIDC discovery document** URL (`.well-known/openid-configuration`), e.g. `https://keycloak.example.com/realms/loom/.well-known/openid-configuration`. Not stored by the CLI directly — append `/.well-known/openid-configuration` to `~/.loom`'s `auth.issuer`. |
| `loom.clientId` | Public OAuth client id for the Device Authorization Grant. Reuse the CLI's client, or register a dedicated public one. |
| `loom.caBundlePath` | Optional PEM file of extra CA certs to trust. Only needed if your IdP's CA isn't in Node's built-in trust store (see **Custom CA / TLS** below). |

**Never set `loom.discoveryUrl` to a bare issuer URL.** The extension does
not assume an issuer's endpoints sit at Keycloak's conventional
`/protocol/openid-connect/...` paths — it fetches this document once and
reads `issuer`, `token_endpoint`, `device_authorization_endpoint`, and the
rest out of it, so it works against any OIDC-compliant IdP.

---

## Signing in

Run **Loom: Sign In** (command palette, or the welcome view's button). This
runs an OAuth2 Device Authorization Grant — the same flow `loom auth login`
uses — and opens your browser with the user code pre-filled.

- Tokens are cached in VS Code's `SecretStorage`, never in settings or
  workspace state.
- An access token nearing expiry is refreshed silently with the cached
  refresh token. If that fails, you're prompted to sign in again.

## Selecting a Tenant

After sign-in the extension lists Tenants available to your identity
(`GET /tenants/mine`) and either auto-selects the only one or prompts you
to pick. Every resource except Tenant itself is nested under
`/tenants/{tenant_id}/...`, so nothing in the sidebar works until a Tenant
is selected. The status bar item (bottom left) always shows the active
Tenant; click it to re-open the picker.

**If every call returns 403 right after signing in:** your identity
probably has no `Principal` record in that Tenant yet. The extension does
**not** auto-register one (unlike `loom auth login`'s first-run
convenience) — that would be a surprising write from a read-only-looking
action. The error message gives you the exact
`loom principal create --tenant-id <id> --kind user --external-id <sub>`
command to run once from a terminal.

---

## The sidebar

The **Loom** activity-bar icon opens one tree view, **Catalog**, with a
category per entity kind (Capabilities, Agents, Models). Expand a category
to list its current versions.

Each item shows `v<n>` and an icon for its **lifecycle state** (hover for
the word):

| Icon | State |
|---|---|
| pencil | Draft |
| eye | In Review |
| verified badge | Approved |
| rocket | Published |
| warning triangle | Deprecated |
| slashed circle | Retired |

Clicking an item:

- **Agent** → opens the form/JSON editor (see below).
- **Capability** → opens the form/JSON editor (see below).
- **Model Endpoint** → opens its full JSON, read-only.

Right-click for more: **Loom: Show Details** (read-only JSON, any entity)
and, on an Agent, **Loom: Edit Prompt** (the system prompt alone in a
plain-text buffer).

Use the **`+`** inline action on a category (or **Loom: Create…**) to
create a new entity of that kind.

---

## Editing an Agent

Clicking an Agent opens a Webview editor with the same **Form / JSON**
toggle, **version picker**, and **Transition** button as the Capability
editor (below).

- **Form** — name and description; `layer` and `memory_scope` dropdowns; a
  **model binding** picker sourced from this Tenant's Model Endpoints (it
  submits the endpoint's `entity_id`, a *floating* reference that always
  resolves to whichever version is current); a large **system prompt**
  textarea; and validated JSON sub-editors for `llm_config` and
  `permission_boundary`.
- **JSON** — the entire request body as one document.

**Save** (or Ctrl/Cmd+S) posts a **new Agent version**
(`POST /agents/{entity_id}/versions`) — append-only/versioned. No conflict
detection: a save always branches from whatever version is current *on the
server*, so editing the same Agent from two places is last-write-wins.

Creating an Agent (`+` on the category) opens this editor with an empty
form; the first save is a `POST /agents` that creates the entity, after
which the panel switches to editing it.

Like the Capability editor, it has a **version picker** (browse prior
versions read-only), a **Transition** button, and a read-only **Version
metadata** footer (maturity, classification, tenant, owner, timestamps,
approver).

**Prompt-only editing:** right-click an Agent → **Loom: Edit Prompt** opens
just `Agent.prompt` in a plain-text buffer. Saving it posts a new version
carrying every other field forward unchanged — handy for long prompts
without the surrounding form.

---

## Editing a Capability

A `Capability` is `name` + `description` + `target_metrics` (a free-form
list of objects — the schema doesn't fix its shape). Clicking a Capability
opens a Webview editor with two views of the same request body, toggled by
the **Form / JSON** switch in the toolbar:

- **Form** — labelled inputs for name and description, plus a small
  validated JSON sub-editor for `target_metrics`.
- **JSON** — the entire request body as one JSON document, exactly as it's
  POSTed to the Catalog API.

Switching views carries your edits across; an invalid `target_metrics`
array or an invalid JSON body blocks the switch and shows why.

**Save** (or Ctrl/Cmd+S) posts a new Capability version
(`POST /capabilities/{entity_id}/versions`) — append-only/versioned, same
as the Agent flow. The panel keeps editing the same entity.

The toolbar also has:

- a **version picker** — select any prior version to view its
  `name`/`description`/`target_metrics` read-only. A save always branches
  from the *current* version, so historical versions aren't editable; a
  banner points you back to the current one.
- a **Transition** button — advances the current version through the
  lifecycle state machine
  (`Draft → In Review → Approved → Published → Deprecated → Retired`). It
  offers only the legal next state(s) and POSTs
  `.../versions/{version}/transitions`. The server still enforces RBAC
  (`catalog:capability:transition`) and any eval gate, so a transition can
  still be refused. The button is hidden when viewing a historical version
  or when the state has no legal successor (Retired).

Creating a Capability (`+` on the category) opens this same editor with an
empty form; the first save is a `POST /capabilities` that creates the
entity, after which the panel switches to editing it.

A **Version metadata** footer shows the viewed version's `maturity`,
`classification`, tenant, owner, created / approved timestamps, and
approver — all **read-only**. The write API has no route to change
`maturity`/`classification` after create today (tracked in issue #4); the
other fields are server-assigned.

---

## Creating entities

Each create flow asks only for what the entity's `*CreateRequest` schema
requires server-side. `tenant_id` / `owner_id` / `created_by_id` /
`lifecycle_state` are never client inputs — the server derives the first
three from your Principal and gates the last behind the lifecycle state
machine.

- **Capability** and **Agent** — open the form/JSON editor (above) in
  create mode. The first save creates the entity.
- **Model Endpoint** — a short wizard: name, protocol, model, optional base
  URL and description. `auth_binding_id` (credential-vault binding) isn't
  set here; wire it via the CLI if the endpoint needs one.

---

## Custom CA / TLS

Outbound calls use Node's built-in `https` module rather than the global
`fetch`, specifically so `loom.caBundlePath` can take effect: `fetch`
(undici) fixes its TLS trust decisions when the extension host process
starts, so a setting picked afterwards can't influence it. If
`loom auth login` needed `LOOM_IDP_CA_BUNDLE` to reach your IdP, point
`loom.caBundlePath` at the same PEM file.
