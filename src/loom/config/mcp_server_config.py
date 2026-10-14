from __future__ import annotations

import pathlib
from collections.abc import Mapping

from pydantic import Field

from .admin_config import AdminConfig
from .auth_config import ServerAuthConfig
from .base import ConfigFile
from .database_config import DatabaseConfig


class McpServerConfig(ConfigFile):
    """Configuration of the Catalog MCP server process. Its `auth.audience`
    is the MCP resource-server client `idp register` creates, *not* the
    REST API's: MCP tool calls are validated against their own audience."""

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    auth: ServerAuthConfig = Field(default_factory=ServerAuthConfig)

    @classmethod
    def from_env(
        cls, config_path: pathlib.Path, environ: Mapping[str, str]
    ) -> McpServerConfig:
        """Build the MCP config from the `LOOM_*` environment variables.
        The audience comes from `LOOM_AUTH_MCP_AUDIENCE`, so one shared
        environment (Compose, a Kubernetes ConfigMap) can serve both the API
        and MCP processes."""
        auth = {
            'audience': environ.get('LOOM_AUTH_MCP_AUDIENCE'),
            'discovery_url': environ.get('LOOM_AUTH_DISCOVERY_URL'),
        }
        data = {
            'config_path': config_path,
            'database': DatabaseConfig.env_values(environ),
            'auth': {k: v for k, v in auth.items() if v},
        }
        return cls.model_validate({k: v for k, v in data.items() if v})

    @classmethod
    def from_admin(
        cls, admin: AdminConfig, config_path: pathlib.Path
    ) -> McpServerConfig:
        """Build the MCP config from what `loom-admin idp register`
        recorded. As for the API, database credentials are not carried
        over."""
        return cls(
            config_path=config_path,
            database=admin.database.location_only(),
            auth=ServerAuthConfig(
                audience=admin.auth.mcp_audience,
                discovery_url=admin.auth.discovery_url,
            ),
        )
