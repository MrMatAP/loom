from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.router_factory import build_versioned_router
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.agent import AgentRead

from .application_service import AgentApplicationService
from .schemas import AgentCreateRequest


def _service_factory(session: AsyncSession) -> AgentApplicationService:
    return AgentApplicationService(UnitOfWork(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/agents',
    tag='agents',
    scope_name='agent',
    read_model=AgentRead,
    create_request_model=AgentCreateRequest,
    service_factory=_service_factory,
)
