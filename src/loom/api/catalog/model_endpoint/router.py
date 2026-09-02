from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.router_factory import build_versioned_router
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.model_endpoint import ModelEndpointRead

from .application_service import ModelEndpointApplicationService
from .schemas import ModelEndpointCreateRequest


def _service_factory(session: AsyncSession) -> ModelEndpointApplicationService:
    return ModelEndpointApplicationService(UnitOfWork(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/model-endpoints',
    tag='model-endpoints',
    scope_name='model_endpoint',
    read_model=ModelEndpointRead,
    create_request_model=ModelEndpointCreateRequest,
    service_factory=_service_factory,
)
