import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column
from app.security import crypto


class HypervisorType(str, enum.Enum):
    ESXI = "esxi"
    # No Proxmox: it was never wired into any user-facing surface (the
    # UI only ever offered ESXi), so ESXi is the only real driver. The
    # Postgres enum type itself (migration 0002) still lists "proxmox"
    # alongside "esxi" - Postgres has no DROP VALUE for enum types, only
    # ADD, so removing it there would mean recreating the type/column
    # rather than a plain migration; not worth it for a value
    # application code never accepts or produces anyway.


class ConnectionStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    OK = "ok"
    FAILED = "failed"


class HypervisorHost(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "hypervisor_hosts"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[HypervisorType] = enum_column(HypervisorType, "hypervisor_type", nullable=False)
    api_endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    tls_verify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_datastore: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_test_status: Mapped[ConnectionStatus] = enum_column(
        ConnectionStatus, "connection_status", default=ConnectionStatus.UNKNOWN, nullable=False
    )
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def credential(self) -> str:
        return crypto.decrypt(self.credential_encrypted)

    @credential.setter
    def credential(self, value: str) -> None:
        self.credential_encrypted = crypto.encrypt(value)
