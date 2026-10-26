import uuid

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(_StrictRequest):
    email: str = Field(min_length=1, max_length=320)
    # Length is policed by the password module; this cap only bounds request size.
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(_StrictRequest):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    """Current-user fields safe to show the user. Never includes the password hash or lockout state."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    institution_id: uuid.UUID | None
    must_change_password: bool


class SessionResponse(BaseModel):
    user: UserOut
    # Session-bound; the SPA keeps it in memory and echoes it in X-CSRF-Token on unsafe requests.
    csrf_token: str


class MessageResponse(BaseModel):
    detail: str
