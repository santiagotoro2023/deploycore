import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column
from app.security import crypto


class DomainJoinTiming(str, enum.Enum):
    ANSWER_FILE = "answer_file"
    POST_INSTALL = "post_install"


class DeploymentTemplate(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "deployment_templates"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # nullable: a template can exist (e.g. seeded demo data) before an
    # operator has uploaded the Windows ISO it deploys from — the
    # provisioning pipeline itself refuses to run without one.
    iso_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("iso_assets.id"), nullable=True
    )
    disk_layout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("disk_layouts.id"), nullable=False
    )

    cpu_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_size_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    network_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    locale: Mapped[str] = mapped_column(String(20), default="en-US", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    keyboard_layout: Mapped[str] = mapped_column(String(20), default="en-US", nullable=False)

    local_admin_password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    domain_join_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    domain_fqdn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_join_account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_join_credential_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    domain_target_ou: Mapped[str | None] = mapped_column(String(512), nullable=True)
    domain_join_timing: Mapped[DomainJoinTiming] = enum_column(
        DomainJoinTiming, "domain_join_timing", default=DomainJoinTiming.ANSWER_FILE, nullable=False
    )

    windows_features: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    post_install_scripts: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    @property
    def local_admin_password(self) -> str:
        return crypto.decrypt(self.local_admin_password_encrypted)

    @local_admin_password.setter
    def local_admin_password(self, value: str) -> None:
        self.local_admin_password_encrypted = crypto.encrypt(value)

    @property
    def domain_join_credential(self) -> str | None:
        if self.domain_join_credential_encrypted is None:
            return None
        return crypto.decrypt(self.domain_join_credential_encrypted)

    @domain_join_credential.setter
    def domain_join_credential(self, value: str) -> None:
        self.domain_join_credential_encrypted = crypto.encrypt(value)
