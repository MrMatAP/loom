from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.router_factory import build_versioned_router
from loom.model.schemas.datasource import DataSourceRead

from .repository import DataSourceRepository
from .schemas import DataSourceCreateRequest
from .service import DataSourceService


def _service_factory(session: AsyncSession) -> DataSourceService:
    return DataSourceService(DataSourceRepository(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/datasources',
    tag='datasources',
    scope_name='datasource',
    read_model=DataSourceRead,
    create_request_model=DataSourceCreateRequest,
    service_factory=_service_factory,
)
