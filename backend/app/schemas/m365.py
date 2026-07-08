from pydantic import BaseModel


class M365ConfigRead(BaseModel):
    tenant_id: str
    client_id: str
    sender_upn: str
    enabled: bool
    configured: bool


class M365ConfigUpdate(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str | None = None
    sender_upn: str
    enabled: bool
