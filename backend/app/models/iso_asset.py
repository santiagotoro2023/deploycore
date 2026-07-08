import enum
import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column


class IsoKind(str, enum.Enum):
    WINDOWS_ISO = "windows_iso"
    VIRTIO_ISO = "virtio_iso"


class UploadStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETE = "complete"
    FAILED = "failed"


class IsoAsset(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "iso_assets"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[IsoKind] = enum_column(IsoKind, "iso_kind", nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    upload_status: Mapped[UploadStatus] = enum_column(
        UploadStatus, "upload_status", default=UploadStatus.PENDING, nullable=False
    )
