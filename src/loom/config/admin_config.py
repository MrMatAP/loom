from pydantic import Field

from .auth_config import AuthConfig
from .base import ConfigFile
from .database_config import DatabaseConfig


class AdminConfig(ConfigFile):
    """Configuration of the `loom-admin` Platform Administrator CLI: the
    privileged (migration) database connection and a record of what `idp
    register` created in the IdP."""

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
