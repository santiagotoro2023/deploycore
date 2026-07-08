import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.models.user import Role
from app.security.auth import validate_password_strength


class UserCreate(BaseModel):
    username: str
    email: EmailStr | None = None
    password: str
    display_name: str
    global_role: Role = Role.NONE

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        validate_password_strength(value)
        return value


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: EmailStr | None = None
    global_role: Role | None = None
    is_active: bool | None = None
    password: str | None = None

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str | None) -> str | None:
        if value:
            validate_password_strength(value)
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str | None
    display_name: str
    global_role: Role
    is_active: bool
    totp_enabled: bool
    has_avatar: bool = False
    org_roles: dict[str, Role] = {}


class OrgRoleAssign(BaseModel):
    org_id: uuid.UUID
    role: Role
