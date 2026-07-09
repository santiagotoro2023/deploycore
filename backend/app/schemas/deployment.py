import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.deployment import DeploymentState, HealthStatus, IpMode, LogLevel

# Windows Setup's specialize pass sets ComputerName as a NetBIOS name, hard
# capped at 15 characters, not the 63-character limit that applies to a
# plain DNS hostname: a longer value doesn't get truncated, it makes Setup
# fail to process the whole answer file during specialize with a generic
# "the computer was unexpectedly restarted" dialog, well after the VM's
# already been created and Setup's copied every file, the least helpful
# point for this to surface at. Confirmed against a real-world report of
# the identical failure with the identical root cause. The excluded
# character set below is Windows' own documented list for ComputerName.
_COMPUTERNAME_MAX_LENGTH = 15
_COMPUTERNAME_INVALID_CHARS = set("{|}~[\\]^':;<=>? ")


def _check_computer_name(value: str, *, label: str = "hostname") -> str:
    if not value.strip():
        raise ValueError(f"{label} cannot be blank")
    if len(value) > _COMPUTERNAME_MAX_LENGTH:
        raise ValueError(
            f'{label} "{value}" is {len(value)} characters; Windows computer names (ComputerName in the '
            f"specialize pass) can be at most {_COMPUTERNAME_MAX_LENGTH}, longer values don't get "
            f"truncated, they make Windows Setup fail during installation instead"
        )
    bad = sorted(set(value) & _COMPUTERNAME_INVALID_CHARS)
    if bad:
        raise ValueError(f'{label} "{value}" contains characters Windows computer names can\'t use: {" ".join(bad)}')
    return value


class DeploymentCreate(BaseModel):
    template_id: uuid.UUID
    hypervisor_host_id: uuid.UUID
    hostname: str
    ip_mode: IpMode = IpMode.DHCP
    static_ip: str | None = None
    static_netmask: str | None = None
    static_gateway: str | None = None
    static_dns: list[str] | None = None

    @field_validator("hostname")
    @classmethod
    def _validate_hostname(cls, value: str) -> str:
        return _check_computer_name(value)


class BulkDeploymentCreate(BaseModel):
    template_id: uuid.UUID
    hypervisor_host_id: uuid.UUID
    hostname_prefix: str
    count: int

    @field_validator("hostname_prefix")
    @classmethod
    def _validate_hostname_prefix(cls, value: str) -> str:
        # Each deployment's actual hostname is f"{prefix}{i:02d}", always a
        # 2-digit suffix (count is capped at 50), so the prefix itself must
        # leave room for those 2 digits under the 15-character limit.
        _check_computer_name(f"{value}00", label="hostname prefix (with its 2-digit suffix)")
        return value


class DeploymentPreviewRequest(BaseModel):
    hostname: str
    ip_mode: IpMode = IpMode.DHCP
    static_ip: str | None = None
    static_netmask: str | None = None
    static_gateway: str | None = None
    static_dns: list[str] | None = None

    @field_validator("hostname")
    @classmethod
    def _validate_hostname(cls, value: str) -> str:
        return _check_computer_name(value)


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
