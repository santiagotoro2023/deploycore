import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.deployment import DeploymentState, HealthStatus, IpMode, LogLevel


class DeploymentCreate(BaseModel):
    template_id: uuid.UUID
    hypervisor_host_id: uuid.UUID
    hostname: str
    ip_mode: IpMode = IpMode.DHCP
    static_ip: str | None = None
    static_netmask: str | None = None
    static_gateway: str | None = None
    static_dns: list[str] | None = None


class BulkDeploymentCreate(BaseModel):
    template_id: uuid.UUID
    hypervisor_host_id: uuid.UUID
    hostname_prefix: str
    count: int


class DeploymentPreviewRequest(BaseModel):
    hostname: str
    ip_mode: IpMode = IpMode.DHCP
    static_ip: str | None = None
    static_netmask: str | None = None
    static_gateway: str | None = None
    static_dns: list[str] | None = None


class DeploymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    template_id: uuid.UUID
    hypervisor_host_id: uuid.UUID
    hostname: str
    ip_mode: IpMode
    static_ip: str | None
    static_netmask: str | None
    static_gateway: str | None
    static_dns: list[str] | None
    state: DeploymentState
    vm_moref: str | None
    error_message: str | None
    retry_count: int
    created_by_user_id: uuid.UUID | None
    last_health_status: HealthStatus
    last_health_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeploymentStateTransitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_state: str
    to_state: str
    occurred_at: datetime
    detail: str | None


class DeploymentHealthCheckRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: HealthStatus
    checked_at: datetime


class DeploymentLogLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    stage: str
    level: LogLevel
    message: str


class AutounattendPreview(BaseModel):
    xml: str


class PowerStateRead(BaseModel):
    power_state: str | None


class PowerAction(BaseModel):
    hard: bool = False
