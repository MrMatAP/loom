from loom.domain.dataproduct import DataProduct as DomainDataProduct
from loom.domain.dataproduct import DataProductLineage as DomainLineage
from loom.persistence.dataproduct import DataProduct as DataProductRow
from loom.persistence.dataproduct import DataProductLineage as LineageRow
from loom.persistence.mappers.base import base_domain_kwargs, base_row_kwargs


def to_domain(row: DataProductRow) -> DomainDataProduct:
    return DomainDataProduct(**base_domain_kwargs(row), contract=row.contract)


def to_row(entity: DomainDataProduct) -> DataProductRow:
    kwargs = base_row_kwargs(entity)
    kwargs.update(contract=entity.contract)
    return DataProductRow(**kwargs)


def lineage_to_domain(row: LineageRow) -> DomainLineage:
    return DomainLineage(
        id=row.id,
        source_datasource_id=row.source_datasource_id,
        source_dataproduct_id=row.source_dataproduct_id,
    )


def lineage_to_row(lineage: DomainLineage, *, dataproduct_row_id) -> LineageRow:
    return LineageRow(
        id=lineage.id,
        dataproduct_id=dataproduct_row_id,
        source_datasource_id=lineage.source_datasource_id,
        source_dataproduct_id=lineage.source_dataproduct_id,
    )
