import uuid

from loom.api.catalog.base_application_service import VersionedApplicationService
from loom.domain.capability import Capability as DomainCapability
from loom.domain.capability import CapabilityRealization as DomainRealization
from loom.persistence.unit_of_work import UnitOfWork
from loom.schemas.capability import CapabilityRead, CapabilityRealizationRead

from .schemas import CapabilityCreateRequest, CapabilityRealizationCreateRequest


def _realization_read(
    realization: DomainRealization, *, capability_row_id: uuid.UUID
) -> CapabilityRealizationRead:
    return CapabilityRealizationRead(
        id=realization.id,
        capability_id=capability_row_id,
        realizing_entity_type=realization.realizing_entity_type,
        realizing_agent_id=realization.realizing_agent_id,
        realizing_skill_id=realization.realizing_skill_id,
        realizing_tool_id=realization.realizing_tool_id,
        contribution_weight=realization.contribution_weight,
    )


class CapabilityApplicationService(
    VersionedApplicationService[DomainCapability, CapabilityRead]
):
    label = 'Capability'
    read_cls = CapabilityRead

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, repository_attr='capabilities')

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: CapabilityCreateRequest,
    ) -> CapabilityRead:
        capability = DomainCapability(
            tenant_id=tenant_id,
            owner_id=created_by_id,
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
        )
        saved = await self._repo.add(capability)
        return self._read(saved)

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: CapabilityCreateRequest,
    ) -> CapabilityRead:
        current = await self._get_current_or_raise(tenant_id, entity_id)
        new_version = current.new_version(
            created_by_id=created_by_id,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
        )
        await self._repo.save(current)
        saved = await self._repo.add(new_version)
        return self._read(saved)

    async def add_realization(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: CapabilityRealizationCreateRequest,
    ) -> CapabilityRealizationRead:
        capability = await self._get_version_or_raise(tenant_id, entity_id, version)
        await self._repo.assert_realizer_in_tenant(
            tenant_id,
            realizing_agent_id=data.realizing_agent_id,
            realizing_skill_id=data.realizing_skill_id,
            realizing_tool_id=data.realizing_tool_id,
        )
        realization = DomainRealization(
            realizing_entity_type=data.realizing_entity_type,
            realizing_agent_id=data.realizing_agent_id,
            realizing_skill_id=data.realizing_skill_id,
            realizing_tool_id=data.realizing_tool_id,
            contribution_weight=data.contribution_weight,
        )
        saved = await self._repo.add_realization(capability.id, realization)
        return _realization_read(saved, capability_row_id=capability.id)

    async def list_realizations(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[CapabilityRealizationRead]:
        capability = await self._get_version_or_raise(tenant_id, entity_id, version)
        realizations = await self._repo.list_realizations(capability.id)
        return [
            _realization_read(r, capability_row_id=capability.id) for r in realizations
        ]
