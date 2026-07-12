import enum
import json
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column, utcnow
from app.security import crypto


class IpMode(str, enum.Enum):
    DHCP = "dhcp"
    STATIC = "static"


class DeploymentState(str, enum.Enum):
    PENDING = "pending"
    CREATING_VM = "creating_vm"
    BOOTING = "booting"
    INSTALLING_OS = "installing_os"
    POST_INSTALL = "post_install"
    CONFIGURING = "configuring"
    COMPLETED = "completed"
    FAILED = "failed"


class LogLevel(str, enum.Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class HealthStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNREACHABLE = "unreachable"


class Deployment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "deployments"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable: deleting a template must not be blocked by deployments
    # already made from it (see the Templates delete confirm dialog's own
    # "deployments already created from it ... are unaffected" wording,
    # which this enforces at the DB level, matching the created_by_user_id
    # and iso_asset_id ON DELETE SET NULL precedents). A deployment that's
    # already completed/failed never needs its template again; one still
    # actively provisioning (or retried after the template was deleted)
    # is guarded explicitly in worker/tasks/provision.py instead of
    # crashing on a None template.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployment_templates.id", ondelete="SET NULL"), nullable=True
    )
    hypervisor_host_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hypervisor_hosts.id"), nullable=False
    )
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)

    ip_mode: Mapped[IpMode] = enum_column(IpMode, "ip_mode", nullable=False)
    static_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    static_netmask: Mapped[str | None] = mapped_column(String(64), nullable=True)
    static_gateway: Mapped[str | None] = mapped_column(String(64), nullable=True)
    static_dns: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    state: Mapped[DeploymentState] = enum_column(
        DeploymentState, "deployment_state", default=DeploymentState.PENDING, nullable=False
    )
    callback_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    callback_token_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Captured from the callback request's own source address (see
    # api/routes/callback.py) the moment it lands - the guest is, by
    # definition, whoever just made that HTTP connection, no need to ask
    # the hypervisor separately. post_install prefers this over
    # HypervisorDriver.get_guest_ip(), which depends entirely on VMware
    # Tools being installed in the guest to report anything at all; a
    # real deployment got stuck on exactly that gap even after Setup and
    # the callback had both already succeeded. Null for a DHCP deployment
    # that only ever advanced via wait_for_callback's WinRM-reachability
    # fallback (no real callback landed to capture an address from).
    guest_reported_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vm_moref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Datastore path of the answer-file media (a floppy image, despite the
    # name, see floppy_builder.py) for this deployment; cleared once
    # deleted from the datastore, success or fail (see provision.py).
    answer_iso_remote_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # The exact autounattend.xml this deployment actually shipped, stored
    # once rendered (see provision.py), not re-derived on demand: the
    # template/disk layout it came from can change afterward, and this
    # should keep reflecting what actually ran, not what those would
    # produce now.
    rendered_autounattend: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "Customize installation" (deployment wizard, after picking a
    # template): a subset of DeploymentTemplate's own fields, overridden
    # for just this one deployment without touching the template itself.
    # Encrypted at rest (same LargeBinary + property pattern as
    # DeploymentTemplate.local_admin_password) since an override can
    # include a plaintext admin password or domain-join credential -
    # see services/template_effective.py for how this actually gets
    # applied (a read-only view layered over the real template, not a
    # copy of it).
    overrides_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Ephemeral, generated right before app installs start in post_install
    # and cleared right after (see provision.py): authenticates the guest's
    # own Invoke-WebRequest calls to GET .../app-assets/{id}/download,
    # there's no user session to authenticate those with, same reasoning
    # as callback_token authenticating the Setup-complete callback. Scoped
    # to the whole deployment (any app asset visible to its org), not one
    # download, since a template can install more than one app.
    app_asset_access_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # SET NULL, not RESTRICT: a user being deleted outright (Settings ->
    # Users) shouldn't be blocked by, or take down, every deployment they
    # ever created, those are real infrastructure history worth keeping.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # periodic post-deploy reachability check, completed deployments only;
    # tracks only the latest result, not a history
    last_health_status: Mapped[HealthStatus] = enum_column(
        HealthStatus, "health_status", default=HealthStatus.UNKNOWN, nullable=False
    )
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Soft delete: hides the deployment from lists/dashboard/detail page
    # without touching its log lines, state transitions, or health checks,
    # those stay in place and reachable by the same endpoints as always,
    # see DELETE .../deployments/{deployment_id} in api/routes/deployments.py.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def overrides(self) -> dict:
        if self.overrides_encrypted is None:
            return {}
        return json.loads(crypto.decrypt(self.overrides_encrypted))

    @overrides.setter
    def overrides(self, value: dict | None) -> None:
        self.overrides_encrypted = crypto.encrypt(json.dumps(value)) if value else None


class DeploymentStateTransition(UUIDPKMixin, Base):
    __tablename__ = "deployment_state_transitions"

    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeploymentLogLine(UUIDPKMixin, Base):
    __tablename__ = "deployment_log_lines"

    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[LogLevel] = enum_column(LogLevel, "log_level", default=LogLevel.INFO, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class DeploymentHealthCheck(UUIDPKMixin, Base):
    """Append-only history of check_deployment_health cron runs, unlike
    Deployment.last_health_status/last_health_checked_at which only track
    the latest result."""

    __tablename__ = "deployment_health_checks"

    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[HealthStatus] = enum_column(HealthStatus, "health_status", nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
