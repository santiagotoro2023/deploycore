import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class DiskLayout(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "disk_layouts"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    layout_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Same {name, script_text} shape as DeploymentTemplate.post_install_scripts,
    # run over WinRM in list order - but as the very first thing
    # run_post_install does, before VMware Tools/roles/apps/the template's
    # own scripts, since these exist for disk/partition fixups (diskpart,
    # DISM, reagentc, bcdedit) that need to happen before anything else
    # touches the disk.
    post_install_scripts: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
