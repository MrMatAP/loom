import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from loom.persistence.agent import Agent as AgentRow
from loom.persistence.db import assert_current_version_in_tenant
from loom.persistence.mappers import agent_mapper
from loom.persistence.model_endpoint import ModelEndpoint as ModelEndpointRow
from loom.persistence.repositories.base_repository import VersionedRepository


class AgentRepository(VersionedRepository):
    """The persistence gateway for the Agent Aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            row_cls=AgentRow,
            to_domain=agent_mapper.to_domain,
            to_row=agent_mapper.to_row,
        )

    async def assert_model_binding_is_current_in_tenant(
        self, tenant_id: uuid.UUID, model_binding_id: uuid.UUID | None
    ) -> None:
        """`model_binding_id` holds a ModelEndpoint.entity_id (floating,
        not a specific version row -- see CONTEXT.md's ModelEndpoint
        entry), so it resolves via the current-version check, not a
        by-row-id one. A no-op for an unset binding: a Draft agent can
        exist before a model is chosen."""
        if model_binding_id is None:
            return
        await assert_current_version_in_tenant(
            self._session, tenant_id, ModelEndpointRow, model_binding_id
        )
