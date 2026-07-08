from pydantic import BaseModel

from app.models.user import Role
from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_totp: bool = False


class TotpRequiredResponse(BaseModel):
    requires_totp: bool = True
    ticket: str


class TotpLoginRequest(BaseModel):
    ticket: str
    code: str


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_url: str


class TotpCodeRequest(BaseModel):
    code: str


class MeResponse(BaseModel):
    user: UserRead
    org_roles: dict[str, Role]
