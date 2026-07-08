import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column, utcnow


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
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployment_templates.id"), nullable=False
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
    vm_moref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    answer_iso_remote_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # periodic post-deploy reachability check, completed deployments only;
    # tracks only the latest result, not a history
    last_health_status: Mapped[HealthStatus] = enum_column(
        HealthStatus, "health_status", default=HealthStatus.UNKNOWN, nullable=False
    )
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
