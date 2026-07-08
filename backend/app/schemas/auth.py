from pydantic import BaseModel, EmailStr

from app.models.user import Role
from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user: UserRead
    org_roles: dict[str, Role]
