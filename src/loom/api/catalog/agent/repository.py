from loom.api.catalog.base import BaseRepository
from loom.persistence.agent import Agent


class AgentRepository(BaseRepository[Agent]):
    """Async persistence access for Agent, scoped by tenant."""

    @property
    def model(self) -> type[Agent]:
        return Agent
