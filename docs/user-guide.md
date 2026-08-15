# Catalog User Guide

Using an already-deployed Catalog: what your assigned role lets you do,
logging in, managing Capabilities/ModelEndpoints/Agents from the CLI, and
reaching the same catalog from an AI agent via MCP. This guide assumes
you're already onboarded -- someone with admin access has given you the
Catalog's URL and provisioned your account. If something here doesn't
match what you're seeing, that's most likely a provisioning gap for your
admin to fix; see [docs/admin-guide.md](admin-guide.md) for what that
looks like on their end.

## Your role

Your admin assigned you one or more roles in the IdP, which decide what
you can do:

| Role | You can |
|---|---|
| `catalog-viewer` | Read Capabilities, Agents, Skills, Tools, DataSources, DataProducts, ModelEndpoints |
| `catalog-editor` | ...plus create and update them |
| `catalog-approver` | Read, plus move them through lifecycle states (approve/publish/deprecate/retire) |
| `catalog-admin` | Full read/write/transition access across all of the above |
| `catalog-platform-admin` | Everything `catalog-admin` has, plus managing Tenants and other users' Principals -- usually held by an administrator, not a typical end user |

A role alone isn't enough to act: your admin also provisions a `Principal`
record for you, scoped to one Tenant. If a command fails with `403`
right after a successful login, that's most likely this record missing
(or provisioned in a different Tenant than the one you're targeting), not
a role problem -- see "Getting help" below.

## Logging in

Point the CLI at your Catalog:

```
loom config set catalog.api_base_url https://catalog.example.com
```

Log in (opens a device-code flow -- a URL and a short code to approve in
any browser, including on a different device):

```
loom auth login
```

If you're provisioned in more than one Tenant, `loom auth login` lists
them and asks you to pick one; otherwise it's selected automatically. Every
subsequent request is scoped to that Tenant. `loom auth set-tenant
<tenant_id>` changes the selection later; `--tenant-id` on an individual
command overrides it for just that call.

`loom auth status` shows whether you're logged in; `loom auth whoami`
shows who the server thinks you are (your `sub`, roles/scopes, and your
selected Tenant) -- useful for diagnosing a `401`/`403`; `loom auth logout`
clears the session.

## Managing entities

Three resources -- `capability`, `model`, `agent` -- each with the same six
verbs: `create`, `list`, `show`, `update`, `versions`, `transition`.
Output renders as `rich` tables, styled like `openstack`/`freeipa`'s CLIs.

A worked example -- register a model endpoint, then an agent bound to it:

```
loom model create claude-opus "Claude Opus" \
  --protocol anthropic_messages --model claude-opus-4

loom agent create triage-bot "Triage Bot" \
  --layer business_tech --memory-scope session \
  --model-binding-id <entity_id from the `model create` output above> \
  --prompt "You triage incoming support tickets."

loom agent list
loom agent show <entity_id>
```

A few things that surprise people the first time:

- **There is no delete, and `update` doesn't overwrite.** This registry is
  append-only/versioned by design -- `update` creates a new version row and
  takes the *entire* set of create flags again (including unchanged ones),
  never mutates the old one in place. `loom capability versions
  <entity_id>` lists every version; `show --version N` fetches a specific
  one instead of the current.
- **Lifecycle is a gate, not a label.** Every entity moves through `draft
  -> in_review -> approved -> published -> deprecated -> retired` via
  `transition`, e.g. `loom capability transition <entity_id> 1 in_review`.
  Skipping states isn't allowed -- the API rejects an illegal transition
  rather than silently applying it. This needs `catalog-approver` (or
  `catalog-admin`), not just `catalog-editor`.
- **JSON-shaped flags** (`--target-metrics`, `--llm-config`,
  `--permission-boundary`) default to an empty array/object if omitted.

`loom <resource> <verb> --help` documents every flag for that command.

## Using it from an AI agent (MCP)

The same use-cases are also reachable as MCP tools, for an agent to call
directly rather than a human running CLI commands -- same entities, same
scope checks, not a separate or weaker path. Auth is still a bearer JWT
from the same IdP; a token from `loom auth login` already carries both the
REST API's and the MCP server's audiences, so logging in once covers both.
Point your MCP client at `{catalog-mcp URL}/mcp` (ask your admin for this
URL -- it's a different service/port than the REST API). Every tool call
takes `tenant_id` as an explicit argument -- there's no separate "selected
Tenant" state on the MCP side the way there is for the CLI, so your MCP
client needs to pass it on every call. Seven tools are registered per
resource (21 total): `create_X`, `get_X`, `list_Xs`, `list_X_versions`,
`get_X_version`, `update_X`, `transition_X` for `capability`/`model`/
`agent`.

## Getting help

If a command fails with a `401`, your token is invalid or expired -- run
`loom auth login` again. If a command fails with a `403`, the token itself
is fine; either you have no `Principal` in the Tenant the request is
targeting (check `--tenant-id`/`loom auth set-tenant` -- if you're
provisioned in more than one Tenant this just means picking the right
one; if you're not provisioned in any, that's an admin-side fix, not
something logging in again resolves), or your role doesn't cover that
action (e.g. `catalog-editor` trying a `transition`, which needs
`catalog-approver`) -- either way, that's your admin's call to adjust. For
anything else, the error
message is the API's own rejection reason (a validation error, an illegal
lifecycle transition); for deployment-level issues (the API unreachable at
all, `500`s on every request), that's your admin's territory too -- see
[docs/admin-guide.md](admin-guide.md#troubleshooting).
