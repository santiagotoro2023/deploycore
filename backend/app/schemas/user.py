import uuid

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.user import Role


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    display_name: str
    global_role: Role = Role.NONE


class UserUpdate(BaseModel):
    display_name: str | None = None
    global_role: Role | None = None
    is_active: bool | None = None
    password: str | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    global_role: Role
    is_active: bool


class OrgRoleAssign(BaseModel):
    org_id: uuid.UUID
    role: Role
