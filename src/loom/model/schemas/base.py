import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from loom.model.enums import Classification, LifecycleState, MaturityLevel


class VersionedEntityCreate(BaseModel):
    """Shared input fields for creating a new version row of any versioned entity."""

    entity_id: uuid.UUID | None = None
    version: int = 1
    is_current: bool = True
    name: str
    description: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    maturity: MaturityLevel = MaturityLevel.EXPERIMENTAL
    classification: Classification = Classification.INTERNAL
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    created_by_id: uuid.UUID
    approved_by_id: uuid.UUID | None = None


class VersionedEntityRead(BaseModel):
    """Shared output fields for any versioned entity."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    version: int
    is_current: bool
    name: str
    description: str | None
    lifecycle_state: LifecycleState
    maturity: MaturityLevel
    classification: Classification
    created_at: datetime.datetime
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    created_by_id: uuid.UUID
    approved_by_id: uuid.UUID | None
    approved_at: datetime.datetime | None
