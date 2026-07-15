import enum
import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column
from app.models.iso_asset import UploadStatus


class AppKind(str, enum.Enum):
    MSI = "msi"
    EXE = "exe"


class AppAsset(UUIDPKMixin, TimestampMixin, Base):
    """An installable piece of software (an agent, a line-of-business app,
    anything with a silent-install flag) that a template can have installed
    over WinRM during post_install, alongside Windows features and
    post-install scripts. Uploaded and stored exactly like an IsoAsset
    (same chunked-upload flow, same org-scoped-or-global visibility), the
    upload_status enum column is literally the same Postgres type,
    reused rather than redefined."""

    __tablename__ = "app_assets"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[AppKind] = enum_column(AppKind, "app_kind", nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # e.g. "/qn /norestart" for an MSI, or "/S" / "/silent" / "/verysilent"
    # for an EXE, whatever that particular installer's own silent-install
    # convention is; a template can override this per attachment.
    default_install_args: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    upload_status: Mapped[UploadStatus] = enum_column(
        UploadStatus, "upload_status", default=UploadStatus.PENDING, nullable=False
    )
    # True for exactly one seeded, global asset: the DeployCore Remote
    # Management Agent installer (see services/remote_agent.py). Lets
    # worker/tasks/provision.py special-case it - a live, per-deployment
    # enrollment token gets appended to its install_args at deploy time,
    # something no ordinary app asset needs (see the 0043 migration's
    # own comment for why a static install_args string can't carry that).
    is_remote_agent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
