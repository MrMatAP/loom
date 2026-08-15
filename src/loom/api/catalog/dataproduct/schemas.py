import uuid

from pydantic import BaseModel


class DataProductCreateRequest(BaseModel):
    name: str
    description: str | None = None
    contract: dict


class DataProductLineageCreateRequest(BaseModel):
    source_datasource_id: uuid.UUID | None = None
    source_dataproduct_id: uuid.UUID | None = None
