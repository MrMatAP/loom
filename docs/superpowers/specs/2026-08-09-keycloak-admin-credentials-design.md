# Keycloak Admin-Credentials Bootstrap — Design

## Problem

`loom idp register-client` currently authenticates to Keycloak with an
Initial Access Token and Dynamic Client Registration (RFC 7591,
`/realms/{realm}/clients-registrations/...`). This is too brittle for what
the command actually needs:

- An Initial Access Token has no Admin API rights, so the follow-up lookup
  of the newly created client's internal id (needed for role management)
  fails.
- Keycloak's DCR endpoints have no concept of role management at all —
  there is no `/clients-registrations/.../roles` endpoint. Declaring the
  Catalog role vocabulary against a DCR-registered client is not something
  the real Keycloak API supports.

The command needs genuine Keycloak Admin REST API access for both steps
(client creation and role declaration), which means it needs an
admin-privileged access token, not a client-registration token.

## Decision: drop DCR, authenticate as an admin

`loom idp register-client` now:

1. Authenticates as a human Keycloak admin via username/password
   (Resource Owner Password Credentials grant — the same mechanism
   `kcadm.sh config credentials` uses), obtaining an admin access token.
2. Uses that token against the genuine Keycloak Admin REST API
   (`/admin/realms/{realm}/clients...`) for both client creation and role
   declaration.

DCR and Initial Access Tokens are removed from this command entirely.

### `IdpAdminClient` protocol — unchanged

`register_client(...)` and `declare_client_roles(client_ref, roles)` keep
their existing signatures (`src/loom/idp/client.py`). This stays an
internal refactor of the Keycloak implementation; the protocol remains
provider-agnostic, so a future non-Keycloak `IdpAdminClient` is unaffected.

### `KeycloakAdminClient` — new `login()` factory, same constructor shape

The constructor keeps taking an already-obtained bearer token (unchanged
shape: `KeycloakAdminClient(issuer, token, *, transport=None)`) — this
keeps the existing `register_client`/`declare_client_roles` tests, which
exercise those methods directly against a token via `httpx.MockTransport`,
working without modification.

A new async classmethod encapsulates the Keycloak-specific login handshake:

```python
@classmethod
async def login(
    cls,
    issuer: str,
    *,
    username: str,
    password: str,
    admin_realm: str = 'master',
    admin_client_id: str = 'admin-cli',
    transport: httpx.AsyncBaseTransport | None = None,
) -> 'KeycloakAdminClient':
    """Authenticate as a Keycloak admin and return a ready client."""
```

It POSTs a `password` grant to
`{issuer_base}/realms/{admin_realm}/protocol/openid-connect/token`
(`issuer_base` derived the same way `_derive_realm_admin_base` already
derives the admin base — strip the target realm off the issuer, splice in
`admin_realm`), extracts `access_token`, and returns
`cls(issuer=issuer, token=access_token, transport=transport)`.

`admin_realm` defaults to `master` (the standard Keycloak superadmin realm)
but is overridable for deployments where admin rights live in the target
realm itself. `admin_client_id` defaults to `admin-cli` (Keycloak's
built-in public client for exactly this grant) and is overridable.

### `register_client` — full Admin API, `Location`-header id, idempotent

```
POST /admin/realms/{realm}/clients
```

with the same client body as today (`clientId`, `name`,
`serviceAccountsEnabled`, `standardFlowEnabled`, `publicClient: false`,
`directAccessGrantsEnabled: false`). On `201`, the internal client id comes
from the `Location` response header (`.../clients/{id}`) — no secondary
lookup call needed.

On `409` (client already exists — the normal case on a bootstrap re-run),
fall back to `GET /admin/realms/{realm}/clients?clientId={client_id}` to
recover the existing client's internal id, matching the idempotent-rerun
property `declare_client_roles` already has.

**New: fetch and surface the client secret.** Nothing today fetches or
prints the generated client secret, so an operator has no way to actually
configure a caller to use the newly registered confidential client. After
resolving the internal id, `GET
/admin/realms/{realm}/clients/{id}/client-secret` and include it in
`ClientRegistrationResult`. `registration_access_token` (DCR-specific) is
no longer produced by this implementation and is always `None`.

### `declare_client_roles` — reverts to genuine Admin API shape

```
POST /admin/realms/{realm}/clients/{client_ref}/roles           # leaf + composite role creation
GET  /admin/realms/{realm}/clients/{client_ref}/roles/{name}     # sub-role lookup
POST /admin/realms/{realm}/clients/{client_ref}/roles/{name}/composites
```

Signature reverts to `(self, client_ref: str, roles: list[RoleDefinition])`,
matching the protocol (closing the current 3-arg mismatch on disk). `409`
handling (idempotent re-run) is unchanged from the existing
`_raise_unless_already_exists` behavior.

## CLI (`src/loom/cli/idp.py`)

Replace `--token` with:

- `--admin-username` (flag) / `LOOM_IDP_ADMIN_USERNAME` (env fallback) /
  interactive `input()` prompt if neither is set.
- `--admin-password` (flag) / `LOOM_IDP_ADMIN_PASSWORD` (env fallback) /
  interactive hidden `getpass.getpass()` prompt if neither is set.
- `--admin-realm` (default `master`).
- `--admin-client-id` (default `admin-cli`).

The command calls `KeycloakAdminClient.login(...)` first, then proceeds
exactly as today: `register_client(...)` → `declare_client_roles(...)`.
Output additionally prints the client secret (labeled "store securely,
shown once", same treatment the registration-access-token print already
used).

## Tests

- `tests/idp/test_keycloak.py`: new coverage for `login()` (mock token
  endpoint, correct realm/client_id in the request, correct token
  extraction). `register_client` tests updated for `Location`-header
  parsing and the `409`→lookup fallback path, plus client-secret fetch.
  `declare_client_roles` tests updated only for the signature fix (base
  URL is already correct in the pre-dirty-edit version).
- `tests/cli/test_idp.py`: updated for the new flags/env-var/prompt
  precedence (flag > env var > prompt), for both username and password
  independently.
- `README.md`: bootstrap instructions rewritten for the admin-credential
  flow.

## Out of scope

- Client-credentials (client_id/secret) admin auth — username/password
  only, per this round's decision.
- Any change to the `IdpAdminClient` protocol or `catalog_role_definitions()`.
- Any change to the Catalog REST API itself.
