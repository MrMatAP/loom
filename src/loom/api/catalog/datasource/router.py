from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.router_factory import build_versioned_router
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.datasource import DataSourceRead

from .application_service import DataSourceApplicationService
from .schemas import DataSourceCreateRequest


def _service_factory(session: AsyncSession) -> DataSourceApplicationService:
    return DataSourceApplicationService(UnitOfWork(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/datasources',
    tag='datasources',
    scope_name='datasource',
    read_model=DataSourceRead,
    create_request_model=DataSourceCreateRequest,
    service_factory=_service_factory,
)
