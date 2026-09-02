import uuid

from pydantic import BaseModel, ConfigDict

from loom.domain.enums import PrincipalKind


class TenantCreate(BaseModel):
    slug: str
    name: str


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str


class TenantUpdate(BaseModel):
    name: str | None = None


class PrincipalCreate(BaseModel):
    kind: PrincipalKind
    external_id: str


class PrincipalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: PrincipalKind
    external_id: str
