from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

ItemT = TypeVar('ItemT')


class Page(BaseModel, Generic[ItemT]):
    """A paginated list response envelope."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int


class PaginationParams:
    """Shared limit/offset query parameters for list endpoints."""

    def __init__(
        self,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> None:
        self.limit = limit
        self.offset = offset
