import uuid

from loom.api.catalog.exceptions import EntityNotFoundError
from loom.model.environment import Environment
from loom.model.schemas.environment import EnvironmentCreate, EnvironmentUpdate

from .repository import EnvironmentRepository


class EnvironmentService:
    """Use-cases for the platform-tier Environment aggregate."""

    def __init__(self, repository: EnvironmentRepository) -> None:
        self._repository = repository

    async def create(self, data: EnvironmentCreate) -> Environment:
        environment = Environment(
            tenant_id=data.tenant_id,
            name=data.name,
            kind=data.kind,
            compute_boundary_ref=data.compute_boundary_ref,
            network_boundary_ref=data.network_boundary_ref,
        )
        return await self._repository.add(environment)

    async def get(self, environment_id: uuid.UUID) -> Environment:
        environment = await self._repository.get(environment_id)
        if environment is None:
            raise EntityNotFoundError(f'Environment {environment_id} not found')
        return environment

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Environment], int]:
        return await self._repository.list_by_tenant(
            tenant_id, limit=limit, offset=offset
        )

    async def update(
        self, environment_id: uuid.UUID, data: EnvironmentUpdate
    ) -> Environment:
        environment = await self.get(environment_id)
        if data.compute_boundary_ref is not None:
            environment.compute_boundary_ref = data.compute_boundary_ref
        if data.network_boundary_ref is not None:
            environment.network_boundary_ref = data.network_boundary_ref
        return await self._repository.save(environment)
