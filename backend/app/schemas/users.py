import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import Role


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UserCreate(_Strict):
    email: str = Field(min_length=1, max_length=320)
    full_name: str = Field(min_length=1, max_length=200)
    role: Role
    institution_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _institution_matches_role(self) -> "UserCreate":
        # Mirrors the database CHECK constraint so callers get a clear 422 instead of a 500.
        if self.role is Role.INSTITUTION_USER and self.institution_id is None:
            raise ValueError("institution_id is required for INSTITUTION_USER")
        if self.role is not Role.INSTITUTION_USER and self.institution_id is not None:
            raise ValueError("institution_id is only allowed for INSTITUTION_USER")
        return self


class UserUpdate(_Strict):
    """Partial update. Absent field = unchanged. Email and password are deliberately not editable."""

    role: Role | None = None
    institution_id: uuid.UUID | None = None
    is_active: bool | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _at_least_one_field_and_no_nulls(self) -> "UserUpdate":
        provided = self.model_fields_set
        if not provided:
            raise ValueError("No fields to update")
        for name in ("role", "is_active", "full_name"):
            if name in provided and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    @field_validator("full_name")
    @classmethod
    def _nonblank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("full_name cannot be blank")
        return v


class UserAdminOut(BaseModel):
    """Administrator view of an account. Never includes the password hash or session data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    institution_id: uuid.UUID | None
    is_active: bool
    must_change_password: bool
    is_locked: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreated(BaseModel):
    user: UserAdminOut
    # Shown exactly once, in this response only. Not stored in recoverable form, logged or audited.
    temporary_password: str


class UserList(BaseModel):
    items: list[UserAdminOut]
    total: int
    limit: int
    offset: int
