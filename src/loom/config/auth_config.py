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
    jwks_uri: str | None = Field(default=None, description='JWKS URI, derived if unset')
    algorithms: list[str] = Field(
        default=['RS256'], description='Accepted JWT algorithms'
    )
    authorization_endpoint: str | None = Field(
        default=None, description='OIDC authorization endpoint, derived if unset'
    )
    token_endpoint: str | None = Field(
        default=None, description='OIDC token endpoint, derived if unset'
    )
    docs_client_id: str = Field(
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
    session: CliSessionConfig = Field(default_factory=CliSessionConfig)
