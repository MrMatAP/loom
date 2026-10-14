from __future__ import annotations

import pathlib
from collections.abc import Mapping

from pydantic import Field

from .admin_config import AdminConfig
from .auth_config import ApiAuthConfig
from .base import ConfigFile
from .database_config import DatabaseConfig


class ApiServerConfig(ConfigFile):
    """Configuration of the Catalog REST API process. Separate from
    `AdminConfig` so the server's runtime database role can differ from the
    privileged one used for migrations, and from `McpServerConfig` because
    the two are registered as distinct resource-server clients in the IdP."""

    api_base_url: str | None = Field(
        default=None,
        description=(
            'Public base URL this API is served from, advertised to clients '
            'via /.well-known/loom-client. Derived from the incoming request '
            'if unset, which is wrong behind a proxy that rewrites the host.'
        ),
    )
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    auth: ApiAuthConfig = Field(default_factory=ApiAuthConfig)

    @classmethod
    def from_env(
        cls, config_path: pathlib.Path, environ: Mapping[str, str]
    ) -> ApiServerConfig:
        """Build the API config from the `LOOM_*` environment variables
        container deployments (Compose, Kubernetes) supply, so secrets can
        come from `secretKeyRef`/`env_file` rather than a mounted file."""
        auth = {
            'audience': environ.get('LOOM_AUTH_AUDIENCE'),
            'discovery_url': environ.get('LOOM_AUTH_DISCOVERY_URL'),
            'swagger_client_id': environ.get('LOOM_AUTH_SWAGGER_CLIENT_ID'),
            'cli_client_id': environ.get('LOOM_AUTH_CLI_CLIENT_ID'),
        }
        data = {
            'config_path': config_path,
            'api_base_url': environ.get('LOOM_CATALOG_API_BASE_URL'),
            'database': DatabaseConfig.env_values(environ),
            'auth': {k: v for k, v in auth.items() if v},
        }
        return cls.model_validate({k: v for k, v in data.items() if v})

    @classmethod
    def from_admin(
        cls,
        admin: AdminConfig,
        config_path: pathlib.Path,
        *,
        api_base_url: str | None = None,
    ) -> ApiServerConfig:
        """Build the API config from what `loom-admin idp register`
        recorded. Database credentials are deliberately not carried over:
        the server's runtime role should differ from the privileged one used
        for migrations, so only the location is copied."""
        return cls(
            config_path=config_path,
            api_base_url=api_base_url.rstrip('/') if api_base_url else None,
            database=admin.database.location_only(),
            auth=ApiAuthConfig(
                audience=admin.auth.audience,
                discovery_url=admin.auth.discovery_url,
                swagger_client_id=admin.auth.swagger_client_id,
                cli_client_id=admin.auth.cli_client_id,
            ),
        )
