from loom.api.catalog.base import BaseRepository
from loom.model.datasource import DataSource


class DataSourceRepository(BaseRepository[DataSource]):
    """Async persistence access for DataSource, scoped by tenant."""

    @property
    def model(self) -> type[DataSource]:
        return DataSource
