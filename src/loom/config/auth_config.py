import uuid

from pydantic import BaseModel, Field, SecretStr, SerializationInfo, field_serializer

DEFAULT_CLI_CLIENT_ID = 'loom-cli'


class CliSessionConfig(BaseModel):
    """This CLI's own cached login state from a prior `loom auth login`."""

    access_token: SecretStr | None = Field(
        default=None, description='Cached access token from the last login'
    )
    refresh_token: SecretStr | None = Field(
        default=None, description='Cached refresh token from the last login'
    )
    expires_at: int | None = Field(
        default=None, description='Unix timestamp the cached access token expires at'
    )
    tenant_id: uuid.UUID | None = Field(
        default=None,
        description=(
            'Locally-selected Tenant, set via `loom auth set-tenant` -- '
            'disambiguates an identity provisioned in more than one Tenant '
            '(see docs/admin-guide.md). Embedded into the URL path of every '
            'request (`CatalogClient.tenant_path`, e.g. '
            '`/api/v1/tenants/{tenant_id}/capabilities`); has no effect for '
            'an identity with only one Principal.'
        ),
    )

    @field_serializer('access_token', 'refresh_token')
    def _serialize_session_secret(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        """Reveal the real secret only when explicitly requested via context."""
        if value is None:
            return None
        if info.context and info.context.get('reveal_secrets'):
            return value.get_secret_value()
        return '**********'


class ServerAuthConfig(BaseModel):
    """What a resource-server process (API or MCP) needs to validate bearer
    tokens: where the IdP's discovery document is and which audience its
    tokens must carry."""

    audience: str = Field(
        default='',
        description=(
            "Expected token audience: this server's own resource-server "
            'client id in the IdP (the API and MCP server are registered '
            'as separate clients, so their audiences differ)'
        ),
    )
    discovery_url: str | None = Field(
        default=None,
        description=(
            "IdP's OIDC discovery document URL (OpenID Connect Discovery "
            "1.0's `.well-known/openid-configuration`). The single source "
            'for everything else about the IdP: `issuer`, '
            '`authorization_endpoint`/`token_endpoint`/`jwks_uri` and the '
            'accepted signing algorithms are all read from this one '
            'document, none are configured separately. Auth is '
            'unconfigured while this is unset.'
        ),
    )


class ApiAuthConfig(ServerAuthConfig):
    """`ServerAuthConfig` plus the public clients the REST API advertises:
    Swagger UI's login and the CLI's device-flow login."""

    swagger_client_id: str = Field(
        default='',
        description=(
            'Public OAuth client id used by Swagger UI to perform an '
            'interactive Authorization Code + PKCE login against the IDP'
        ),
    )
    cli_client_id: str = Field(
        default=DEFAULT_CLI_CLIENT_ID,
        description=(
            'Public OAuth client id used by `loom auth login` to perform an '
            'interactive Device Authorization Grant login against the IDP. '
            'Advertised to the CLI via /.well-known/loom-client.'
        ),
    )


class AuthConfig(BaseModel):
    """What `loom-admin idp register` created in the IdP -- every client id
    in one place. `ApiServerConfig.from_admin` and
    `McpServerConfig.from_admin` each derive their own server's slice."""

    audience: str = Field(default='', description='Expected token audience')
    discovery_url: str | None = Field(
        default=None,
        description=(
            "IdP's OIDC discovery document URL (OpenID Connect Discovery "
            "1.0's `.well-known/openid-configuration`). The single source "
            'for everything else about the IdP: `issuer`, '
            '`authorization_endpoint`/`token_endpoint`/`jwks_uri` and the '
            'accepted signing algorithms are all read from this one '
            'document, none are configured separately. Auth is '
            'unconfigured while this is unset.'
        ),
    )
    swagger_client_id: str = Field(
        default='',
        description=(
            'Public OAuth client id used by Swagger UI to perform an '
            'interactive Authorization Code + PKCE login against the IDP'
        ),
    )
    cli_client_id: str = Field(
        default=DEFAULT_CLI_CLIENT_ID,
        description=(
            'Public OAuth client id used by `loom auth login` to perform an '
            'interactive Device Authorization Grant login against the IDP. '
            'Advertised to the CLI via /.well-known/loom-client.'
        ),
    )
    mcp_audience: str = Field(
        default='',
        description=(
            'Expected token audience for the MCP server -- a separate '
            'resource-server client from the RESTful API, so MCP tool calls '
            'are validated against their own audience rather than sharing '
            "the API's"
        ),
    )


class ClientAuthConfig(BaseModel):
    """What `loom` needs to log in: where the IdP's discovery document is,
    which public client to use, and the cached session itself."""

    discovery_url: str | None = Field(
        default=None,
        description=(
            "IdP's OIDC discovery document URL. Cached from the server's "
            '/.well-known/loom-client by `loom auth login --server`.'
        ),
    )
    cli_client_id: str | None = Field(
        default=DEFAULT_CLI_CLIENT_ID,
        description=(
            'Override for the public OAuth client id used for login. Unset '
            f'means the server-advertised value, else {DEFAULT_CLI_CLIENT_ID!r}.'
        ),
    )
    discovery_cli_client_id: str | None = Field(
        default=None,
        description=(
            'The client id the server advertised on the last `loom auth '
            'login --server`. Kept apart from `cli_client_id` so a local '
            'override stays distinguishable from a cached server value.'
        ),
    )
    session: CliSessionConfig = Field(default_factory=CliSessionConfig)

    def effective_cli_client_id(self) -> str:
        """Local override, else the server-advertised id, else the default."""
        return (
            self.cli_client_id or self.discovery_cli_client_id or DEFAULT_CLI_CLIENT_ID
        )
