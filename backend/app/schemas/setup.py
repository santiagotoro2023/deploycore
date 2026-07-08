from pydantic import BaseModel, EmailStr, field_validator

from app.security.auth import validate_password_strength


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupRequest(BaseModel):
    instance_name: str
    admin_username: str
    admin_email: EmailStr | None = None
    admin_display_name: str
    admin_password: str

    @field_validator("admin_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        validate_password_strength(value)
        return value
