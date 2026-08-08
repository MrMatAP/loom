from pydantic import Field

from .base import RootConfigAware


class AuthConfig(RootConfigAware):
    """OIDC settings for validating bearer tokens presented to the API."""

    issuer: str = Field(default='', description='OIDC issuer URL')
    audience: str = Field(default='', description='Expected token audience')
    jwks_uri: str | None = Field(default=None, description='JWKS URI, derived if unset')
    algorithms: list[str] = Field(
        default=['RS256'], description='Accepted JWT algorithms'
    )
