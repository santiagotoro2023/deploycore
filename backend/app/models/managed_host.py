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
    either path ends up in exactly the same place, an enrolled row
    here, with no special-casing downstream in the connect flow for
    "how did this host come to exist."

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

    # Permanent per-host identifier, generated when the host row is created
    # (before the agent ever runs). Doubles as two things: the agent's
    # one-time enrollment call (see api/routes/remote_agent.py) is
    # authenticated by this - the same "guest already knows a secret
    # DeployCore handed it in advance" pattern as Deployment.callback_token,
    # not a user session (there isn't one on the machine running the agent) -
    # and, after enrollment, the identifier the agent presents (alongside
    # agent_key below) on every reconnect of its persistent control channel
    # (see remote-agent/PROTOCOL.md). Never rotated, so "single-use" no
    # longer describes it - kept as the one stable handle across the agent's
    # whole lifetime instead of minting a second identifier for no reason.
    enroll_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=lambda: secrets.token_hex(32))
    enrolled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Minted by DeployCore itself (not the agent) at the end of the one-time
    # enrollment call, and returned to the agent exactly once - the agent
    # persists it locally (DPAPI-protected) and presents it as the
    # X-Agent-Key header on every control-channel connection afterward. This
    # is the only credential anywhere in the native protocol; nothing is
    # ever prompted for or shown to an operator (see PROTOCOL.md's "Why no
    # password" section). Server-minted rather than agent-reported (the old
    # rustdesk_key's pattern) so a not-yet-trusted caller never gets to
    # dictate its own long-term credential.
    agent_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Optional, operator-entered RDP credentials for this host - separate
    # from agent_key above (that's the control channel's own credential,
    # server-minted, never chosen by anyone). These are a real Windows
    # account's own username/password, used to auto-authenticate a real,
    # separate "Connect" RDP session (see services/remote_session.py /
    # RemoteSession.tsx) - so encrypted at rest the same way agent_key
    # already is.
    rdp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rdp_password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    @property
    def agent_key(self) -> str | None:
        return crypto.decrypt(self.agent_key_encrypted) if self.agent_key_encrypted else None

    @agent_key.setter
    def agent_key(self, value: str) -> None:
        self.agent_key_encrypted = crypto.encrypt(value)

    @property
    def rdp_password(self) -> str | None:
        return crypto.decrypt(self.rdp_password_encrypted) if self.rdp_password_encrypted else None

    @rdp_password.setter
    def rdp_password(self, value: str | None) -> None:
        # Falsy (None or "") clears it rather than encrypting an empty
        # string - lets the update route treat an explicitly-blank value as
        # "remove the stored password" (see ManagedHostUpdate's own docstring).
        self.rdp_password_encrypted = crypto.encrypt(value) if value else None

    @property
    def rdp_password_set(self) -> bool:
        """Read-only signal for the frontend (ManagedHostRead) - whether a
        password is on file, without ever returning the plaintext over the
        list/get routes. The plaintext is only ever returned by the dedicated
        rdp-credentials route, fetched on demand right when Connect is
        clicked, not as part of routine host reads."""
        return self.rdp_password_encrypted is not None
