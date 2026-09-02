from sqlalchemy.ext.asyncio import AsyncSession

from loom.persistence.mappers import model_endpoint_mapper
from loom.persistence.model_endpoint import ModelEndpoint as ModelEndpointRow
from loom.persistence.repositories.base_repository import VersionedRepository


class ModelEndpointRepository(VersionedRepository):
    """The persistence gateway for the ModelEndpoint Aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            row_cls=ModelEndpointRow,
            to_domain=model_endpoint_mapper.to_domain,
            to_row=model_endpoint_mapper.to_row,
        )
