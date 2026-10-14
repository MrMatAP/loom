from pydantic import Field

from .auth_config import ClientAuthConfig
from .base import ConfigFile
from .catalog_config import CatalogConfig


class UserConfig(ConfigFile):
    """Configuration of the `loom` user CLI: how to reach the Catalog API and
    the cached login session. Deliberately holds no database or IdP admin
    settings -- `loom` only ever talks to the authenticated API."""

    auth: ClientAuthConfig = Field(default_factory=ClientAuthConfig)
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
