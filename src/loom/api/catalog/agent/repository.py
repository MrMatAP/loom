from loom.api.catalog.base import BaseRepository
from loom.model.agent import Agent


class AgentRepository(BaseRepository[Agent]):
    """Async persistence access for Agent, scoped by tenant."""

    @property
    def model(self) -> type[Agent]:
        return Agent
