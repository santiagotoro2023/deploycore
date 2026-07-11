import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    deployment_id: uuid.UUID | None
    message: str
    read: bool
    created_at: datetime


class UnreadCount(BaseModel):
    count: int


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email_on_start: bool
    email_on_complete: bool
    email_on_failed: bool
    email_on_health_degraded: bool
    teams_on_start: bool
    teams_on_complete: bool
    teams_on_failed: bool
    teams_on_health_degraded: bool


class NotificationPreferenceUpdate(BaseModel):
    email_on_start: bool
    email_on_complete: bool
    email_on_failed: bool
    email_on_health_degraded: bool
    teams_on_start: bool
    teams_on_complete: bool
    teams_on_failed: bool
    teams_on_health_degraded: bool


class NotificationTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    email_subject: str
    email_body: str
    teams_message: str


class NotificationTemplateUpdate(BaseModel):
    email_subject: str
    email_body: str
    teams_message: str
