from loom.domain.agent import Agent as DomainAgent
from loom.persistence.agent import Agent as AgentRow
from loom.persistence.mappers.base import base_domain_kwargs, base_row_kwargs


def to_domain(row: AgentRow) -> DomainAgent:
    return DomainAgent(
        **base_domain_kwargs(row),
        layer=row.layer,
        model_binding_id=row.model_binding_id,
        llm_config=row.llm_config,
        prompt=row.prompt,
        memory_scope=row.memory_scope,
        permission_boundary=row.permission_boundary,
    )


def to_row(entity: DomainAgent) -> AgentRow:
    kwargs = base_row_kwargs(entity)
    kwargs.update(
        layer=entity.layer,
        model_binding_id=entity.model_binding_id,
        llm_config=entity.llm_config,
        prompt=entity.prompt,
        memory_scope=entity.memory_scope,
        permission_boundary=entity.permission_boundary,
    )
    return AgentRow(**kwargs)
