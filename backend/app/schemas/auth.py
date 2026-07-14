from pydantic import BaseModel, field_validator

from app.models.user import Role
from app.schemas.user import UserRead
from app.security.auth import validate_password_strength


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        validate_password_strength(value)
        return value


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
