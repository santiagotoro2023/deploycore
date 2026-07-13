import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, utcnow
from app.security import crypto


class Webhook(UUIDPKMixin, TimestampMixin, Base):
    """Org-scoped, generic outbound webhook, ticketing-tool-agnostic: Jira
    Automation/ServiceNow/Zapier/n8n consume this with their own inbound
    webhook trigger rather than DeployCore integrating any one of them
    directly. events is a list of event-type strings, e.g.
    ["deployment.failed", "deployment.complete"]."""

    __tablename__ = "webhooks"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    @property
    def secret(self) -> str:
        return crypto.decrypt(self.secret_encrypted)

    @secret.setter
    def secret(self, value: str) -> None:
        self.secret_encrypted = crypto.encrypt(value)


class WebhookDelivery(UUIDPKMixin, Base):
    __tablename__ = "webhook_deliveries"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    response_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
