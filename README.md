# Loom

An AI-Enabled Distributed Systems IDE

## Database

Loom stores its registry (Capabilities, Agents, Skills, Tools, DataSources,
DataProducts, and governance/eval records) in PostgreSQL. Configure the
connection once:

    loom config set database.host db.example.com
    loom config set database.username loom
    loom config set database.password <secret>

Then apply migrations — no separate `alembic` install or invocation needed:

    loom db upgrade

Other database commands: `loom db downgrade <revision>`, `loom db current`,
`loom db history`, `loom db revision -m "message" [--autogenerate]`.

## Catalog API

The Catalog component is a standalone FastAPI service fronting the
Capability/Agent/Skill/Tool/DataSource/DataProduct aggregates, plus a
platform-bootstrap tier (Tenant/Principal/Environment). It requires a
configured database (see above) and an OIDC issuer:

    loom config set auth.issuer https://idp.example/realms/loom
    loom config set auth.audience loom-catalog-api

Run it:

    loom-catalog-api

Or directly via uvicorn for development: `uvicorn loom.api.catalog.main:app --reload`.

Every request requires a Bearer JWT carrying a `tenant_id` claim and
either a `scope` claim (space-separated `catalog:{resource}:{action}`
scopes) or a `roles` claim (`catalog-viewer`, `catalog-editor`,
`catalog-approver`, `catalog-admin`, `catalog-platform-admin`). See
`docs/superpowers/specs/2026-08-08-catalog-api-design.md` for the full
access-rights model.

### Registering the client with your IDP

For Keycloak: generate a token capable of both Dynamic Client Registration
and realm role management (a plain "Initial Access Token" from Client
Registration settings is DCR-only and typically insufficient — use an
access token from a realm-admin service account via `client_credentials`),
then:

    loom idp register-client --issuer-url https://idp.example/realms/loom \
      --token <token> --client-id loom-catalog-api

This registers the OAuth client and declares all 24 leaf scopes plus the 5
composite roles (`catalog-viewer`, `catalog-editor`, `catalog-approver`,
`catalog-admin`, `catalog-platform-admin`) as roles under that client.
Assigning those roles to actual users/service accounts is a separate step
performed in the Keycloak admin console.
