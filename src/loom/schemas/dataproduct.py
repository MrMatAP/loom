import uuid

from pydantic import BaseModel, ConfigDict

from loom.schemas.base import VersionedEntityCreate, VersionedEntityRead


class DataProductCreate(VersionedEntityCreate):
    contract: dict


class DataProductRead(VersionedEntityRead):
    contract: dict


class DataProductLineageCreate(BaseModel):
    dataproduct_id: uuid.UUID
    source_datasource_id: uuid.UUID | None = None
    source_dataproduct_id: uuid.UUID | None = None


class DataProductLineageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataproduct_id: uuid.UUID
    source_datasource_id: uuid.UUID | None
    source_dataproduct_id: uuid.UUID | None
