import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InstitutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    state: str
    institution_type: str
    contact_email: str | None
    status: str
    created_at: datetime


class InstitutionList(BaseModel):
    items: list[InstitutionOut]
    total: int
    limit: int
    offset: int
