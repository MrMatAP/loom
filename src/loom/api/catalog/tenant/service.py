import uuid

from loom.api.catalog.exceptions import EntityNotFoundError
from loom.model.schemas.tenant import TenantCreate, TenantUpdate
from loom.model.tenant import Tenant

from .repository import TenantRepository


class TenantService:
    """Use-cases for the platform-tier Tenant aggregate."""

    def __init__(self, repository: TenantRepository) -> None:
        self._repository = repository

    async def create(self, data: TenantCreate) -> Tenant:
        tenant = Tenant(slug=data.slug, name=data.name)
        return await self._repository.add(tenant)

    async def get(self, tenant_id: uuid.UUID) -> Tenant:
        tenant = await self._repository.get(tenant_id)
        if tenant is None:
            raise EntityNotFoundError(f'Tenant {tenant_id} not found')
        return tenant

    async def list_all(self, *, limit: int, offset: int) -> tuple[list[Tenant], int]:
        return await self._repository.list_all(limit=limit, offset=offset)

    async def update(self, tenant_id: uuid.UUID, data: TenantUpdate) -> Tenant:
        tenant = await self.get(tenant_id)
        if data.name is not None:
            tenant.name = data.name
        return await self._repository.save(tenant)
