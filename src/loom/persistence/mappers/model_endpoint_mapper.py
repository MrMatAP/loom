from loom.domain.model_endpoint import ModelEndpoint as DomainModelEndpoint
from loom.persistence.mappers.base import base_domain_kwargs, base_row_kwargs
from loom.persistence.model_endpoint import ModelEndpoint as ModelEndpointRow


def to_domain(row: ModelEndpointRow) -> DomainModelEndpoint:
    return DomainModelEndpoint(
        **base_domain_kwargs(row),
        protocol=row.protocol,
        model=row.model,
        base_url=row.base_url,
        auth_binding_id=row.auth_binding_id,
    )


def to_row(entity: DomainModelEndpoint) -> ModelEndpointRow:
    kwargs = base_row_kwargs(entity)
    kwargs.update(
        protocol=entity.protocol,
        model=entity.model,
        base_url=entity.base_url,
        auth_binding_id=entity.auth_binding_id,
    )
    return ModelEndpointRow(**kwargs)
