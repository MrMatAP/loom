import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.datasource import DataSource
from loom.model.enums import LifecycleState
from loom.model.tenant import Principal

from .repository import DataSourceRepository
from .schemas import DataSourceCreateRequest


class DataSourceService:
    """Use-cases for the DataSource aggregate."""

    def __init__(self, repository: DataSourceRepository) -> None:
        self._repository = repository

    async def _resolve_owner_id(
        self, tenant_id: uuid.UUID, owner_id: uuid.UUID | None, fallback: uuid.UUID
    ) -> uuid.UUID:
        if owner_id is None:
            return fallback
        await self._repository.assert_same_tenant(tenant_id, Principal, owner_id)
        return owner_id

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSource:
        owner_id = await self._resolve_owner_id(tenant_id, data.owner_id, created_by_id)
        datasource = DataSource(
            tenant_id=tenant_id,
            owner_id=owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        return await self._repository.add(datasource)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DataSource:
        datasource = await self._repository.get_current(tenant_id, entity_id)
        if datasource is None:
            raise EntityNotFoundError(f'DataSource {entity_id} not found')
        return datasource

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataSource:
        datasource = await self._repository.get_version(tenant_id, entity_id, version)
        if datasource is None:
            detail = f'DataSource {entity_id} version {version} not found'
            raise EntityNotFoundError(detail)
        return datasource

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataSource]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'DataSource {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataSource], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSource:
        current = await self.get_current(tenant_id, entity_id)
        owner_id = await self._resolve_owner_id(
            tenant_id, data.owner_id, current.owner_id
        )
        current.is_current = False
        await self._repository.save(current)
        new_version = DataSource(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> DataSource:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = (
                f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            )
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)
