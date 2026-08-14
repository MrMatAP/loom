from pydantic import Field, SecretStr, SerializationInfo, field_serializer

from .base import RootConfigAware


class CliSessionConfig(RootConfigAware):
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


class AuthConfig(RootConfigAware):
    """OIDC settings for validating bearer tokens presented to the API."""

    issuer: str = Field(default='', description='OIDC issuer URL')
    audience: str = Field(default='', description='Expected token audience')
    algorithms: list[str] = Field(
        default=['RS256'], description='Accepted JWT algorithms'
    )
    discovery_url: str | None = Field(
        default=None,
        description=(
            "IdP's OIDC discovery document URL (OpenID Connect Discovery "
            "1.0's `.well-known/openid-configuration`), derived from "
            '`issuer` if unset. `authorization_endpoint`/`token_endpoint`/'
            "`jwks_uri` are all read from this one document -- there's no "
            'per-endpoint override anymore. Only needed as an override when '
            "the issuer used for token validation isn't also where the "
            'IdP serves its metadata (e.g. an internal issuer URL behind a '
            'proxy with a different externally-reachable discovery '
            'document).'
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
        default='',
        description=(
            'Public OAuth client id used by `loom auth login` to perform an '
            'interactive Device Authorization Grant login against the IDP'
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
    session: CliSessionConfig = Field(default_factory=CliSessionConfig)
