# Catalog User Guide

Using an already-deployed Catalog: logging in, managing Capabilities /
ModelEndpoints / Agents from the CLI, and reaching the same catalog from
an AI agent via MCP. If you need to stand the Catalog up first, see
[docs/admin-guide.md](admin-guide.md) instead -- this guide assumes
someone already has and can tell you its URL.

## One-time setup

You need two things from your admin: the Catalog's URL, and to be
registered with its IDP (assigned one of `catalog-viewer`/
`catalog-editor`/`catalog-approver`/`catalog-admin`/
`catalog-platform-admin`, or an equivalent set of scopes). Scopes alone
aren't the whole story -- your admin also needs to have given your IDP
account a `tenant_id` attribute and provisioned a matching `Principal`
record for you in the Catalog itself, or every request will `401` no
matter how many scopes your token carries (see
[docs/admin-guide.md](admin-guide.md)'s Troubleshooting section).

Point the CLI at the API:

```
loom config set catalog.api_base_url https://catalog.example.com
```

Log in (opens a device-code flow -- a URL and a short code to approve in
any browser, including on a different device):

```
loom auth login
```

`loom auth status` shows whether you're logged in; `loom auth logout`
clears the session. Full detail on this flow, including troubleshooting a
failed login redirect, is in README.md's ["CLI device-code
login"](../README.md#cli-device-code-login).

## Managing entities

Three resources -- `capability`, `model`, `agent` -- each with the same
six verbs: `create`, `list`, `show`, `update`, `versions`, `transition`.
Output renders as `rich` tables, styled like `openstack`/`freeipa`'s CLIs.
Full flag reference (including every resource-specific `create`/`update`
flag) is in README.md's ["Managing Catalog entities from the
CLI"](../README.md#managing-catalog-entities-from-the-cli) -- this section
is a quick-start, not the full reference.

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

- **There is no delete, and `update` doesn't overwrite.** This registry
  is append-only/versioned by design -- `update` creates a new version row
  and takes the *entire* set of create flags again (including unchanged
  ones), never mutates the old one in place. `loom capability versions
  <entity_id>` lists every version; `show --version N` fetches a specific
  one instead of the current.
- **Lifecycle is a gate, not a label.** Every entity moves through
  `draft -> in_review -> approved -> published -> deprecated -> retired`
  via `transition`, e.g. `loom capability transition <entity_id> 1
  in_review`. Skipping states isn't allowed -- the API rejects an illegal
  transition rather than silently applying it.
- **`--owner-id` defaults to you**, and JSON-shaped flags
  (`--target-metrics`, `--llm-config`, `--permission-boundary`) default to
  an empty array/object if omitted.

## Using it from an AI agent (MCP)

The same use-cases are also reachable as MCP tools, for an agent to call
directly rather than a human running CLI commands -- same entities, same
auth (a bearer JWT from the same IDP), same scope checks; not a separate,
weaker path. Point your MCP client at `{catalog-mcp URL}/mcp` (your admin
has this URL -- it's a different service/port than the REST API). Full
tool list (7 per resource: `create_X`/`get_X`/`list_Xs`/
`list_X_versions`/`get_X_version`/`update_X`/`transition_X`) is in
README.md's ["Catalog MCP server"](../README.md#catalog-mcp-server)
section.

## Getting help

`loom <resource> <verb> --help` documents every flag for that command.
If a command fails with a `401`, read the reason the CLI prints along
with it: if it names an expired/invalid token, `loom auth login` again;
if it instead points at your account not being fully provisioned (no
`tenant_id`, no `Principal` record), logging in again won't help --
that's an admin-side fix, see [docs/admin-guide.md](admin-guide.md)'s
Troubleshooting section. If a command fails with anything else, the error
message is the API's own rejection reason (a validation error, an illegal
lifecycle transition, a missing scope) -- for deployment-level issues
(the API unreachable at all, `500`s on every request), see
[docs/admin-guide.md](admin-guide.md)'s Troubleshooting section instead,
that's your admin's territory.
