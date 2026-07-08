import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WebhookCreate(BaseModel):
    name: str
    url: str
    secret: str
    enabled: bool = True
    events: list[str] = []


class WebhookUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    enabled: bool | None = None
    events: list[str] | None = None


class WebhookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    url: str
    enabled: bool
    events: list[str]


class WebhookDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    status_code: int | None
    success: bool
    response_snippet: str | None
    occurred_at: datetime


class WebhookTestResult(BaseModel):
    ok: bool
    status_code: int | None
    message: str
