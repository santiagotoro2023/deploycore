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
    cores_per_socket: int = 1
    ram_mb: int
    disk_size_gb: int
    disk_provisioning: str = "thin"
    firmware: str
    scsi_controller: str
    network_name: str
    network_adapter_type: str = "vmxnet3"
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
    async def send_enter_keypress(self, vm_ref: str) -> None:
        """Sends a single synthetic Enter keypress to the VM's virtual
        keyboard, used to dismiss Windows Setup's "Press any key to boot
        from CD or DVD..." prompt (shown for optical boot media
        specifically, not USB/HDD) since there's nobody at the console to
        press it. Best-effort from callers' point of view, sent as a
        burst rather than once since the prompt's timing varies."""

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
    async def upload_iso_to_datastore(self, local_path: str, remote_name: str, skip_if_exists: bool = False) -> str:
        """Returns the datastore-relative remote path. `skip_if_exists`
        lets a caller re-attach content already uploaded for a previous
        deployment (the shared Windows ISO, keyed by a stable name) without
        re-uploading it."""

    @abstractmethod
    async def delete_iso_from_datastore(self, remote_path: str) -> None: ...
