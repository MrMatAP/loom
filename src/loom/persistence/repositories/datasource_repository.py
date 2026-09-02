from sqlalchemy.ext.asyncio import AsyncSession

from loom.persistence.datasource import DataSource as DataSourceRow
from loom.persistence.mappers import datasource_mapper
from loom.persistence.repositories.base_repository import VersionedRepository


class DataSourceRepository(VersionedRepository):
    """The persistence gateway for the DataSource Aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            row_cls=DataSourceRow,
            to_domain=datasource_mapper.to_domain,
            to_row=datasource_mapper.to_row,
        )
