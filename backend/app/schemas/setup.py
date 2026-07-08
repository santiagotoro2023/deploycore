from pydantic import BaseModel, EmailStr


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupRequest(BaseModel):
    instance_name: str
    admin_email: EmailStr
    admin_display_name: str
    admin_password: str
