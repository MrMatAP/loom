from loom.domain.capability import Capability as DomainCapability
from loom.domain.capability import CapabilityRealization as DomainRealization
from loom.persistence.capability import Capability as CapabilityRow
from loom.persistence.capability import CapabilityRealization as RealizationRow
from loom.persistence.mappers.base import base_domain_kwargs, base_row_kwargs


def to_domain(row: CapabilityRow) -> DomainCapability:
    return DomainCapability(**base_domain_kwargs(row), target_metrics=row.target_metrics)


def to_row(entity: DomainCapability) -> CapabilityRow:
    kwargs = base_row_kwargs(entity)
    kwargs.update(target_metrics=entity.target_metrics)
    return CapabilityRow(**kwargs)


def realization_to_domain(row: RealizationRow) -> DomainRealization:
    return DomainRealization(
        id=row.id,
        realizing_entity_type=row.realizing_entity_type,
        realizing_agent_id=row.realizing_agent_id,
        realizing_skill_id=row.realizing_skill_id,
        realizing_tool_id=row.realizing_tool_id,
        contribution_weight=row.contribution_weight,
    )


def realization_to_row(
    realization: DomainRealization, *, capability_row_id
) -> RealizationRow:
    return RealizationRow(
        id=realization.id,
        capability_id=capability_row_id,
        realizing_entity_type=realization.realizing_entity_type,
        realizing_agent_id=realization.realizing_agent_id,
        realizing_skill_id=realization.realizing_skill_id,
        realizing_tool_id=realization.realizing_tool_id,
        contribution_weight=realization.contribution_weight,
    )
