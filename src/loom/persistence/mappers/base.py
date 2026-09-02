"""Shared field mapping for the `AggregateRoot`-common columns every
Mapper needs (CONTEXT.md's "Mapper" entry) -- factored out so each
entity's own mapper only has to spell out its entity-specific fields.
"""

from typing import Any


def base_domain_kwargs(row: Any) -> dict:
    return {
        'id': row.id,
        'entity_id': row.entity_id,
        'version': row.version,
        'is_current': row.is_current,
        'tenant_id': row.tenant_id,
        'owner_id': row.owner_id,
        'created_by_id': row.created_by_id,
        'approved_by_id': row.approved_by_id,
        'name': row.name,
        'description': row.description,
        'lifecycle_state': row.lifecycle_state,
        'maturity': row.maturity,
        'classification': row.classification,
        'created_at': row.created_at,
        'approved_at': row.approved_at,
    }


def base_row_kwargs(entity: Any) -> dict:
    kwargs = {
        'id': entity.id,
        'entity_id': entity.entity_id,
        'version': entity.version,
        'is_current': entity.is_current,
        'tenant_id': entity.tenant_id,
        'owner_id': entity.owner_id,
        'created_by_id': entity.created_by_id,
        'approved_by_id': entity.approved_by_id,
        'name': entity.name,
        'description': entity.description,
        'lifecycle_state': entity.lifecycle_state,
        'maturity': entity.maturity,
        'classification': entity.classification,
        'approved_at': entity.approved_at,
    }
    # created_at is server_default=now() -- omit rather than pass None so
    # a brand-new row still gets the DB's clock, not a NOT NULL violation.
    if entity.created_at is not None:
        kwargs['created_at'] = entity.created_at
    return kwargs
