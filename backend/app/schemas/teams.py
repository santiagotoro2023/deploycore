from pydantic import BaseModel


class TeamsConfigRead(BaseModel):
    tenant_id: str
    client_id: str
    teams_app_id: str
    enabled: bool
    configured: bool


class TeamsConfigUpdate(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str | None = None
    teams_app_id: str
    enabled: bool
