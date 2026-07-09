from app.hypervisors.base import ConnectionResult, HypervisorDriver, PowerState, VmSpec

_NOT_IMPLEMENTED = "Proxmox driver not implemented in this MVP (see ARCHITECTURE.md non-goals)"


class ProxmoxDriver(HypervisorDriver):
    """Structural stub proving HypervisorDriver already fits a second
    hypervisor. Backed by proxmoxer once implemented; every method raises
    until then."""

    async def test_connection(self) -> ConnectionResult:
        return ConnectionResult(ok=False, message=_NOT_IMPLEMENTED)

    async def create_vm(self, spec: VmSpec) -> str:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def attach_iso(self, vm_ref: str, iso_path: str, unit: int) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def detach_iso(self, vm_ref: str, unit: int) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def attach_floppy(self, vm_ref: str, floppy_path: str, unit: int = 0) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def detach_floppy(self, vm_ref: str, unit: int = 0) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def set_boot_order(self, vm_ref: str, device_order: list[str]) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def power_on(self, vm_ref: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def send_enter_keypress(self, vm_ref: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def power_off(self, vm_ref: str, hard: bool = False) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def get_power_state(self, vm_ref: str) -> PowerState:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def get_guest_ip(self, vm_ref: str) -> str | None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def delete_vm(self, vm_ref: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def upload_iso_to_datastore(self, local_path: str, remote_name: str, skip_if_exists: bool = False) -> str:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def delete_iso_from_datastore(self, remote_path: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)
