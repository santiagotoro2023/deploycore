import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.hypervisor import ConnectionStatus, HypervisorType


class HypervisorHostCreate(BaseModel):
    name: str
    type: HypervisorType
    api_endpoint: str
    username: str
    credential: str
    tls_verify: bool = True
    default_datastore: str | None = None


class HypervisorHostUpdate(BaseModel):
    name: str | None = None
    api_endpoint: str | None = None
    username: str | None = None
    credential: str | None = None
    tls_verify: bool | None = None
    default_datastore: str | None = None


class HypervisorHostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    type: HypervisorType
    api_endpoint: str
    username: str
    tls_verify: bool
    default_datastore: str | None
    last_test_status: ConnectionStatus
    last_test_at: datetime | None
    last_test_message: str | None
