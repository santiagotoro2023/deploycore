import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin, utcnow


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
    """Per-user opt-in/out for M365 email delivery of the same events the
    in-app Notification above already covers. Lazily created with these
    defaults on first GET /api/notification-preferences."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    email_on_start: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_on_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_on_failed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_on_health_degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
