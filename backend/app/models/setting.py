import enum
import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column


class SettingScope(str, enum.Enum):
    GLOBAL = "global"
    ORG = "org"
    TEMPLATE = "template"
    DEPLOYMENT = "deployment"


class Setting(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint("scope", "org_id", "template_id", "deployment_id", "key", name="uq_setting_scope_key"),
    )

    scope: Mapped[SettingScope] = enum_column(SettingScope, "setting_scope", nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployment_templates.id", ondelete="CASCADE"), nullable=True
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
