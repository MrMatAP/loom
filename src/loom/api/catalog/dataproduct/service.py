import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.dataproduct import DataProduct, DataProductLineage
from loom.model.datasource import DataSource
from loom.model.enums import LifecycleState

from .repository import DataProductRepository
from .schemas import DataProductCreateRequest, DataProductLineageCreateRequest


class DataProductService:
    """Use-cases for the DataProduct aggregate."""

    def __init__(self, repository: DataProductRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProduct:
        dataproduct = DataProduct(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            contract=data.contract,
        )
        return await self._repository.add(dataproduct)

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DataProduct:
        dataproduct = await self._repository.get_current(tenant_id, entity_id)
        if dataproduct is None:
            raise EntityNotFoundError(f'DataProduct {entity_id} not found')
        return dataproduct

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataProduct:
        dataproduct = await self._repository.get_version(tenant_id, entity_id, version)
        if dataproduct is None:
            detail = f'DataProduct {entity_id} version {version} not found'
            raise EntityNotFoundError(detail)
        return dataproduct

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataProduct]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'DataProduct {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataProduct], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProduct:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = DataProduct(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            # Ownership carries over from the prior version -- there's no
            # more owner_id input to override it with (always inferred,
            # never caller-supplied).
            owner_id=current.owner_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            contract=data.contract,
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
    ) -> DataProduct:
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

    async def add_lineage(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: DataProductLineageCreateRequest,
    ) -> DataProductLineage:
        dataproduct = await self.get_version(tenant_id, entity_id, version)
        for model, referenced_id in (
            (DataSource, data.source_datasource_id),
            (DataProduct, data.source_dataproduct_id),
        ):
            if referenced_id is not None:
                await self._repository.assert_same_tenant(
                    tenant_id, model, referenced_id
                )
        lineage = DataProductLineage(
            dataproduct_id=dataproduct.id,
            source_datasource_id=data.source_datasource_id,
            source_dataproduct_id=data.source_dataproduct_id,
        )
        return await self._repository.add_lineage(lineage)

    async def list_lineage(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[DataProductLineage]:
        dataproduct = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_lineage(dataproduct.id)
