from loom.domain.datasource import DataSource as DomainDataSource
from loom.persistence.datasource import DataSource as DataSourceRow
from loom.persistence.mappers.base import base_domain_kwargs, base_row_kwargs


def to_domain(row: DataSourceRow) -> DomainDataSource:
    return DomainDataSource(
        **base_domain_kwargs(row),
        kind=row.kind,
        connection_binding_id=row.connection_binding_id,
    )


def to_row(entity: DomainDataSource) -> DataSourceRow:
    kwargs = base_row_kwargs(entity)
    kwargs.update(kind=entity.kind, connection_binding_id=entity.connection_binding_id)
    return DataSourceRow(**kwargs)
