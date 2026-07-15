import secrets
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.security import crypto


class ManagedHost(Base, UUIDPKMixin, TimestampMixin):
    """A server or workstation reachable through Remote Management -
    either linked back to a Deployment this project provisioned (see
    deployment_id), or added independently for a machine that wasn't:
    the whole point of the agent being just the (rebranded) RustDesk
    client is that either path ends up in exactly the same place, an
    enrolled row here, with no special-casing downstream in the
    connect flow for "how did this host come to exist."

    Always org-scoped (unlike DiskLayout/AppAsset/DeploymentTemplate,
    there's no "global" concept here - a specific physical or virtual
    machine belongs to exactly one organization, it isn't a reusable
    template other orgs would ever attach)."""

    __tablename__ = "managed_hosts"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable + ON DELETE SET NULL, same reasoning as Deployment.template_id:
    # deleting the deployment this host came from must not be blocked by
    # (or cascade into deleting) a still-perfectly-valid remote-management
    # enrollment - the agent keeps working regardless of what DeployCore's
    # own deployment history looks like afterward.
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Single-use per-host token, generated when the host row is created
    # (before the agent ever runs) - the agent's own one-time enrollment
    # call (see api/routes/remote_agent.py) is authenticated by this, the same
    # "guest already knows a secret DeployCore handed it in advance"
    # pattern as Deployment.callback_token, not a user session (there
    # isn't one on the machine running the agent).
    enroll_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=lambda: secrets.token_hex(32))
    enrolled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Both populated by that one enrollment call, not chosen by
    # DeployCore: the RustDesk client generates its own ID locally on
    # first run, and the enrollment script (bundled in the agent
    # installer) generates the permanent/unattended-access password
    # locally too, immediately reporting both home rather than either
    # ever being baked into the shared installer itself.
    rustdesk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rustdesk_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def rustdesk_key(self) -> str | None:
        return crypto.decrypt(self.rustdesk_key_encrypted) if self.rustdesk_key_encrypted else None

    @rustdesk_key.setter
    def rustdesk_key(self, value: str) -> None:
        self.rustdesk_key_encrypted = crypto.encrypt(value)
