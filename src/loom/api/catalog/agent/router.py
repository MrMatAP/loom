from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.router_factory import build_versioned_router
from loom.model.schemas.agent import AgentRead

from .repository import AgentRepository
from .schemas import AgentCreateRequest
from .service import AgentService


def _service_factory(session: AsyncSession) -> AgentService:
    return AgentService(AgentRepository(session))


router, _service = build_versioned_router(
    prefix='/tenants/{tenant_id}/agents',
    tag='agents',
    scope_name='agent',
    read_model=AgentRead,
    create_request_model=AgentCreateRequest,
    service_factory=_service_factory,
)
