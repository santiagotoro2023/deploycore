import enum
from abc import ABC, abstractmethod

from pydantic import BaseModel


class PowerState(str, enum.Enum):
    POWERED_ON = "poweredOn"
    POWERED_OFF = "poweredOff"
    SUSPENDED = "suspended"


class ConnectionResult(BaseModel):
    ok: bool
    message: str


class VmSpec(BaseModel):
    name: str
    cpu_count: int
    ram_mb: int
    disk_size_gb: int
    firmware: str
    scsi_controller: str
    network_name: str
    datastore: str | None = None


class HypervisorDriver(ABC):
    """Shared VM lifecycle contract for every hypervisor backend. `ESXiDriver`
    is the fully-implemented driver for this MVP; `ProxmoxDriver` stubs the
    same surface so adding Proxmox later is additive, not a rewrite."""

    def __init__(self, host) -> None:
        self.host = host

    @abstractmethod
    async def test_connection(self) -> ConnectionResult: ...

    @abstractmethod
    async def create_vm(self, spec: VmSpec) -> str:
        """Returns the hypervisor-side VM identity (e.g. ESXi MOID)."""

    @abstractmethod
    async def attach_iso(self, vm_ref: str, iso_path: str, unit: int) -> None: ...

    @abstractmethod
    async def detach_iso(self, vm_ref: str, unit: int) -> None: ...

    @abstractmethod
    async def set_boot_order(self, vm_ref: str, device_order: list[str]) -> None: ...

    @abstractmethod
    async def power_on(self, vm_ref: str) -> None: ...

    @abstractmethod
    async def power_off(self, vm_ref: str, hard: bool = False) -> None: ...

    @abstractmethod
    async def get_power_state(self, vm_ref: str) -> PowerState: ...

    @abstractmethod
    async def get_guest_ip(self, vm_ref: str) -> str | None:
        """Guest-reported IP (e.g. via VMware Tools). Used to reach the
        guest over WinRM right after first boot, before any post-install
        static network reconfiguration happens."""

    @abstractmethod
    async def delete_vm(self, vm_ref: str) -> None: ...

    @abstractmethod
    async def upload_iso_to_datastore(self, local_path: str, remote_name: str) -> str:
        """Returns the datastore-relative remote path."""

    @abstractmethod
    async def delete_iso_from_datastore(self, remote_path: str) -> None: ...
