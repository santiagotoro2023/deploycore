import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, utcnow


class Notification(UUIDPKMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationPreference(UUIDPKMixin, Base):
    """Per-user opt-in/out for M365 email and Teams delivery of the same
    events the in-app Notification above already covers. Lazily created
    with these defaults on first GET /api/notification-preferences."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    email_on_start: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_on_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_on_failed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Delivered to the same address as email (User.email doubles as the
    # Teams UPN - true for the overwhelming majority of M365 tenants,
    # where a user's primary email and UPN match).
    teams_on_start: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    teams_on_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    teams_on_failed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class NotificationTemplate(UUIDPKMixin, TimestampMixin, Base):
    """One row per event type (start/complete/failed),
    instance-wide (like M365Config/TeamsConfig): the actual subject/body/
    message text sent for that event, fully operator-editable instead of
    a hardcoded string. Seeded with today's hardcoded defaults by
    migration 0032 so behavior doesn't change until someone edits one.
    {placeholder} fields available per event are documented in
    services/notifications.py's EVENT_CONTEXT_FIELDS."""

    __tablename__ = "notification_templates"

    event_type: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    email_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_body: Mapped[str] = mapped_column(Text, nullable=False)
    teams_message: Mapped[str] = mapped_column(Text, nullable=False)
