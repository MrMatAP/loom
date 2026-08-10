from pydantic import Field

from .base import RootConfigAware


class CatalogConfig(RootConfigAware):
    """Where `loom {capability,model,agent} create` etc. reach the running
    Catalog API, as an authenticated HTTP client (see `catalog_client.py`) --
    distinct from `database`, which is the DSN the API server itself binds
    to."""

    api_base_url: str = Field(
        default='http://localhost:8000',
        description='Base URL the Catalog API is served from',
    )
