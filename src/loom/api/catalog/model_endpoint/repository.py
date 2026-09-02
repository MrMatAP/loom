from loom.api.catalog.base import BaseRepository
from loom.persistence.model_endpoint import ModelEndpoint


class ModelEndpointRepository(BaseRepository[ModelEndpoint]):
    """Async persistence access for ModelEndpoint, scoped by tenant."""

    @property
    def model(self) -> type[ModelEndpoint]:
        return ModelEndpoint
