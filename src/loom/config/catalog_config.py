from pydantic import BaseModel, Field


class CatalogConfig(BaseModel):
    """Where `loom {capability,model,agent} create` etc. reach the running
    Catalog API, as an authenticated HTTP client (see `catalog_client.py`)."""

    api_base_url: str = Field(
        default='http://localhost:8000',
        description='Base URL the Catalog API is served from',
    )
