import uuid

from loom.api.catalog.exceptions import EntityNotFoundError
from loom.model.schemas.tenant import PrincipalCreate
from loom.model.tenant import Principal

from .repository import PrincipalRepository


class PrincipalService:
    """Use-cases for the platform-tier Principal aggregate."""

    def __init__(self, repository: PrincipalRepository) -> None:
        self._repository = repository

    async def create(self, data: PrincipalCreate) -> Principal:
        principal = Principal(
            tenant_id=data.tenant_id,
            kind=data.kind,
            external_id=data.external_id,
        )
        return await self._repository.add(principal)

    async def get(self, principal_id: uuid.UUID) -> Principal:
        principal = await self._repository.get(principal_id)
        if principal is None:
            raise EntityNotFoundError(f'Principal {principal_id} not found')
        return principal

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Principal], int]:
        return await self._repository.list_by_tenant(
            tenant_id, limit=limit, offset=offset
        )
