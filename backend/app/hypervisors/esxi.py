import asyncio
import logging
import ssl

import httpx
from pyVim import connect
from pyVim.task import WaitForTask
from pyVmomi import vim

from app.hypervisors.base import ConnectionResult, HypervisorDriver, PowerState, VmSpec
from app.models.hypervisor import HypervisorHost

logger = logging.getLogger(__name__)

_POWER_STATE_MAP = {
    vim.VirtualMachinePowerState.poweredOn: PowerState.POWERED_ON,
    vim.VirtualMachinePowerState.poweredOff: PowerState.POWERED_OFF,
    vim.VirtualMachinePowerState.suspended: PowerState.SUSPENDED,
}


class ESXiDriver(HypervisorDriver):
    """pyvmomi is synchronous; every call below runs in a worker thread via
    asyncio.to_thread so the arq event loop is never blocked on network I/O
    to the hypervisor."""

    def __init__(self, host: HypervisorHost) -> None:
        super().__init__(host)

    def _connect_sync(self):
        ssl_context = None
        if not self.host.tls_verify:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        return connect.SmartConnect(
            host=self.host.api_endpoint,
            user=self.host.username,
            pwd=self.host.credential,
            sslContext=ssl_context,
        )

    async def _connect(self):
        return await asyncio.to_thread(self._connect_sync)

    async def _disconnect(self, service_instance) -> None:
        await asyncio.to_thread(connect.Disconnect, service_instance)

    def _find_vm_sync(self, service_instance, vm_ref: str):
        content = service_instance.RetrieveContent()
        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        try:
            for candidate in view.view:
                if candidate._moId == vm_ref:
                    return candidate
        finally:
            view.Destroy()
        raise LookupError(f"VM {vm_ref} not found on {self.host.api_endpoint}")

    def _default_resource_pool_and_folder_sync(self, service_instance):
        # Assumes a single datacenter/single standalone host inventory, which
        # holds for a direct ESXi host connection (as opposed to vCenter
        # managing many clusters). Revisit if this driver is ever pointed at
        # a multi-cluster vCenter instead of a standalone host.
        content = service_instance.RetrieveContent()
        datacenter = content.rootFolder.childEntity[0]
        host_folder = datacenter.hostFolder
        compute_resource = host_folder.childEntity[0]
        return compute_resource.resourcePool, datacenter.vmFolder

    def _create_vm_sync(self, spec: VmSpec) -> str:
        service_instance = self._connect_sync()
        try:
            resource_pool, vm_folder = self._default_resource_pool_and_folder_sync(
                service_instance
            )
            datastore_name = spec.datastore or self.host.default_datastore
            controller = vim.vm.device.VirtualDeviceSpec()
            controller.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            # LSI Logic SAS, not VMware's own PVSCSI: Windows has no inbox
            # driver for PVSCSI at all, a paravirtualized boot disk on it
            # can't be recognized during Setup or on subsequent boots
            # without injecting the VMware Tools PVSCSI driver into the
            # install media first, which this pipeline never does. LSI
            # Logic SAS needs no driver injection, every Windows Server
            # version has it inbox, at the cost of PVSCSI's (mainly
            # high-IOPS-workload) performance edge, worth it for a
            # zero-touch pipeline that has to just work unattended.
            controller.device = vim.vm.device.VirtualLsiLogicSASController()
            controller.device.key = 1000
            controller.device.busNumber = 0
            controller.device.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing

            disk_spec = vim.vm.device.VirtualDeviceSpec()
            disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            disk_spec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
            disk_spec.device = vim.vm.device.VirtualDisk()
            disk_spec.device.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
            disk_spec.device.backing.diskMode = "persistent"
            # thin: thinProvisioned only. thick lazy zeroed: neither flag (the
            # vSphere API's own default, zeroed on first write). thick eager
            # zeroed: eagerlyScrub, zeroed up front at creation time.
            disk_spec.device.backing.thinProvisioned = spec.disk_provisioning == "thin"
            disk_spec.device.backing.eagerlyScrub = spec.disk_provisioning == "thick_eager_zeroed"
            disk_spec.device.unitNumber = 0
            disk_spec.device.capacityInKB = spec.disk_size_gb * 1024 * 1024
            disk_spec.device.controllerKey = 1000

            nic_device_cls = {
                "vmxnet3": vim.vm.device.VirtualVmxnet3,
                "e1000": vim.vm.device.VirtualE1000,
                "e1000e": vim.vm.device.VirtualE1000e,
            }.get(spec.network_adapter_type, vim.vm.device.VirtualVmxnet3)
            nic_spec = vim.vm.device.VirtualDeviceSpec()
            nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            nic_spec.device = nic_device_cls()
            nic_spec.device.backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
            nic_spec.device.backing.deviceName = spec.network_name
            nic_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
            nic_spec.device.connectable.startConnected = True
            if spec.mac_address:
                nic_spec.device.addressType = "manual"
                nic_spec.device.macAddress = spec.mac_address

            config = vim.vm.ConfigSpec()
            config.name = spec.name
            config.numCPUs = spec.cpu_count
            config.numCoresPerSocket = spec.cores_per_socket
            config.memoryMB = spec.ram_mb
            # There's no per-template tracking of which Windows release an
            # ISO actually installs, and the dedicated per-release ids
            # (windows2019srv_64Guest, windows2019srvNext_64Guest for 2022,
            # windows2022srvNext_64Guest for 2025, ...) each depend on the
            # target host's specific API/hardware-version level supporting
            # that exact string, an older or differently-configured host
            # can reject one of those outright as an invalid guestId.
            # windows9Server64Guest ("Windows Server 2016 or later, 64-bit")
            # is the broadly-compatible catch-all VMware documents for
            # exactly this, supported on effectively every ESXi version
            # this app would ever target; it only affects vCenter's default
            # icon/optimization hints, not the actual installation.
            config.guestId = "windows9Server64Guest"
            config.firmware = "efi" if spec.firmware == "efi" else "bios"
            config.files = vim.vm.FileInfo(
                vmPathName=f"[{datastore_name}] {spec.name}"
            )
            config.deviceChange = [controller, disk_spec, nic_spec]

            task = vm_folder.CreateVM_Task(config=config, pool=resource_pool)
            # WaitForTask's return value is the task's State enum
            # ("success"/"error"), not its result, it just raises on error
            # (the default raiseOnError=True) so getting past this line at
            # all means it succeeded; the created VM itself is task.info.result.
            WaitForTask(task)
            vm = task.info.result

            # Best-effort, separate reconfigure so a failure here can never
            # affect VM creation itself: without VMware Tools installed (this
            # pipeline never installs it), a fresh Windows guest only has the
            # default PS/2 mouse, which the ESXi/vSphere web console can't
            # track properly, the cursor doesn't reliably show up or move
            # with the actual pointer at all. A USB 3.0 (xHCI) controller is
            # the standard fix, confirmed against Packer's own vsphere-iso
            # builder source (its "xhci" option constructs exactly
            # types.VirtualUSBXHCIController with no other properties set,
            # same as here, specifically "needed for mouse during install
            # without VMware Tools"): ESXi presents an absolute-positioning
            # USB tablet over it automatically, no VMware Tools required.
            try:
                usb_spec = vim.vm.device.VirtualDeviceSpec()
                usb_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
                usb_spec.device = vim.vm.device.VirtualUSBXHCIController()
                WaitForTask(vm.ReconfigVM_Task(spec=vim.vm.ConfigSpec(deviceChange=[usb_spec])))
            except Exception:  # noqa: BLE001 - cosmetic, never worth failing VM creation over
                logger.exception("esxi: failed to add a USB controller to %s, console mouse may not work", spec.name)

            return vm._moId
        finally:
            connect.Disconnect(service_instance)

    async def test_connection(self) -> ConnectionResult:
        try:
            service_instance = await self._connect()
            await self._disconnect(service_instance)
            return ConnectionResult(ok=True, message="connected")
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
            return ConnectionResult(ok=False, message=str(exc))

    async def create_vm(self, spec: VmSpec) -> str:
        return await asyncio.to_thread(self._create_vm_sync, spec)

    def _cdrom_device_sync(self, service_instance, vm_ref: str, unit: int):
        vm = self._find_vm_sync(service_instance, vm_ref)
        for device in vm.config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualCdrom) and device.unitNumber == unit:
                return vm, device
        return vm, None

    def _attach_iso_sync(self, vm_ref: str, iso_path: str, unit: int) -> None:
        service_instance = self._connect_sync()
        try:
            vm, existing = self._cdrom_device_sync(service_instance, vm_ref, unit)
            device_spec = vim.vm.device.VirtualDeviceSpec()
            device_spec.device = existing or vim.vm.device.VirtualCdrom()
            device_spec.operation = (
                vim.vm.device.VirtualDeviceSpec.Operation.edit
                if existing
                else vim.vm.device.VirtualDeviceSpec.Operation.add
            )
            if not existing:
                device_spec.device.unitNumber = unit
                device_spec.device.controllerKey = 200
                device_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
            device_spec.device.backing = vim.vm.device.VirtualCdrom.IsoBackingInfo(
                fileName=iso_path
            )
            device_spec.device.connectable.connected = True
            device_spec.device.connectable.startConnected = True
            config = vim.vm.ConfigSpec(deviceChange=[device_spec])
            WaitForTask(vm.ReconfigVM_Task(spec=config))
        finally:
            connect.Disconnect(service_instance)

    async def attach_iso(self, vm_ref: str, iso_path: str, unit: int) -> None:
        await asyncio.to_thread(self._attach_iso_sync, vm_ref, iso_path, unit)

    def _attach_floppy_sync(self, vm_ref: str, floppy_path: str, unit: int) -> None:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            existing = next(
                (
                    d
                    for d in vm.config.hardware.device
                    if isinstance(d, vim.vm.device.VirtualFloppy) and d.unitNumber == unit
                ),
                None,
            )
            device_spec = vim.vm.device.VirtualDeviceSpec()
            device_spec.device = existing or vim.vm.device.VirtualFloppy()
            device_spec.operation = (
                vim.vm.device.VirtualDeviceSpec.Operation.edit
                if existing
                else vim.vm.device.VirtualDeviceSpec.Operation.add
            )
            if not existing:
                device_spec.device.unitNumber = unit
                # No explicit controllerKey: unlike IDE/SCSI, there's no
                # separate VirtualFloppyController device type in the API
                # to reference, the floppy bus is implicit on every VM.
                device_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
            device_spec.device.backing = vim.vm.device.VirtualFloppy.ImageBackingInfo(fileName=floppy_path)
            device_spec.device.connectable.connected = True
            device_spec.device.connectable.startConnected = True
            config = vim.vm.ConfigSpec(deviceChange=[device_spec])
            WaitForTask(vm.ReconfigVM_Task(spec=config))
        finally:
            connect.Disconnect(service_instance)

    async def attach_floppy(self, vm_ref: str, floppy_path: str, unit: int = 0) -> None:
        await asyncio.to_thread(self._attach_floppy_sync, vm_ref, floppy_path, unit)

    def _detach_floppy_sync(self, vm_ref: str, unit: int) -> None:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            existing = next(
                (
                    d
                    for d in vm.config.hardware.device
                    if isinstance(d, vim.vm.device.VirtualFloppy) and d.unitNumber == unit
                ),
                None,
            )
            if existing is None:
                return
            device_spec = vim.vm.device.VirtualDeviceSpec()
            device_spec.device = existing
            device_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
            config = vim.vm.ConfigSpec(deviceChange=[device_spec])
            WaitForTask(vm.ReconfigVM_Task(spec=config))
        finally:
            connect.Disconnect(service_instance)

    async def detach_floppy(self, vm_ref: str, unit: int = 0) -> None:
        await asyncio.to_thread(self._detach_floppy_sync, vm_ref, unit)

    def _detach_iso_sync(self, vm_ref: str, unit: int) -> None:
        service_instance = self._connect_sync()
        try:
            vm, existing = self._cdrom_device_sync(service_instance, vm_ref, unit)
            if existing is None:
                return
            device_spec = vim.vm.device.VirtualDeviceSpec()
            device_spec.device = existing
            device_spec.device.backing = vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo()
            device_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
            config = vim.vm.ConfigSpec(deviceChange=[device_spec])
            WaitForTask(vm.ReconfigVM_Task(spec=config))
        finally:
            connect.Disconnect(service_instance)

    async def detach_iso(self, vm_ref: str, unit: int) -> None:
        await asyncio.to_thread(self._detach_iso_sync, vm_ref, unit)

    def _set_boot_order_sync(self, vm_ref: str, device_order: list[str]) -> None:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            boot_devices = []
            for device_type in device_order:
                if device_type == "cdrom":
                    boot_devices.append(vim.vm.BootOptions.BootableCdromDevice())
                elif device_type == "disk":
                    # BootableDiskDevice.deviceKey is required, it has to
                    # point at the actual disk device's key on this VM
                    # (assigned by ESXi at creation time), an empty one
                    # makes the whole bootOrder list invalid.
                    disk = next(
                        d for d in vm.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)
                    )
                    boot_devices.append(vim.vm.BootOptions.BootableDiskDevice(deviceKey=disk.key))
            config = vim.vm.ConfigSpec(
                bootOptions=vim.vm.BootOptions(
                    bootOrder=boot_devices,
                    # A freshly attached CD-ROM's backing isn't always
                    # fully connected the instant the VM powers on, so the
                    # very first boot attempt can race it and report every
                    # device (including a perfectly good ISO) as
                    # unsuccessful. bootRetryEnabled defaults to false,
                    # which per VMware's own docs means the VM then "waits
                    # indefinitely for you to initiate boot retry", i.e.
                    # it just sits there until someone manually reselects
                    # a device, exactly what was happening. A short delay
                    # plus automatic retry gives devices time to settle
                    # and recovers on its own instead.
                    bootDelay=5000,
                    bootRetryEnabled=True,
                    bootRetryDelay=10000,
                )
            )
            WaitForTask(vm.ReconfigVM_Task(spec=config))
        finally:
            connect.Disconnect(service_instance)

    async def set_boot_order(self, vm_ref: str, device_order: list[str]) -> None:
        await asyncio.to_thread(self._set_boot_order_sync, vm_ref, device_order)

    def _power_on_sync(self, vm_ref: str) -> None:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            WaitForTask(vm.PowerOnVM_Task())
        finally:
            connect.Disconnect(service_instance)

    async def power_on(self, vm_ref: str) -> None:
        await asyncio.to_thread(self._power_on_sync, vm_ref)

    def _send_enter_keypress_sync(self, vm_ref: str) -> None:
        """Microsoft's own Windows Setup boot loader shows a "Press any
        key to boot from CD or DVD..." prompt with a short timeout
        whenever it's booting from optical media specifically (not from
        USB or a hard disk), in both BIOS and EFI mode, so with nobody at
        the console this VM would otherwise silently fall through to the
        next (empty) boot device every single time. This sends one Enter
        keypress via the vSphere USB HID scan-code API, the same
        technique real-world unattended-install automation (e.g.
        Packer's vSphere builder) uses for exactly this; callers send a
        burst of these since the prompt's exact timing varies."""
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            key_event = vim.UsbScanCodeSpecKeyEvent()
            key_event.usbHidCode = (0x28 << 16) | 0x07  # USB HID usage 0x28: Keyboard Return (ENTER)
            spec = vim.UsbScanCodeSpec()
            spec.keyEvents = [key_event]
            vm.PutUsbScanCodes(spec)
        finally:
            connect.Disconnect(service_instance)

    async def send_enter_keypress(self, vm_ref: str) -> None:
        await asyncio.to_thread(self._send_enter_keypress_sync, vm_ref)

    def _power_off_sync(self, vm_ref: str, hard: bool) -> None:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            if hard:
                WaitForTask(vm.PowerOffVM_Task())
            else:
                vm.ShutdownGuest()
        finally:
            connect.Disconnect(service_instance)

    async def power_off(self, vm_ref: str, hard: bool = False) -> None:
        await asyncio.to_thread(self._power_off_sync, vm_ref, hard)

    def _get_power_state_sync(self, vm_ref: str) -> PowerState:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            return _POWER_STATE_MAP[vm.runtime.powerState]
        finally:
            connect.Disconnect(service_instance)

    async def get_power_state(self, vm_ref: str) -> PowerState:
        return await asyncio.to_thread(self._get_power_state_sync, vm_ref)

    def _get_guest_ip_sync(self, vm_ref: str) -> str | None:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            return vm.guest.ipAddress if vm.guest else None
        finally:
            connect.Disconnect(service_instance)

    async def get_guest_ip(self, vm_ref: str) -> str | None:
        return await asyncio.to_thread(self._get_guest_ip_sync, vm_ref)

    def _delete_vm_sync(self, vm_ref: str) -> None:
        service_instance = self._connect_sync()
        try:
            vm = self._find_vm_sync(service_instance, vm_ref)
            if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOn:
                WaitForTask(vm.PowerOffVM_Task())
            WaitForTask(vm.Destroy_Task())
        finally:
            connect.Disconnect(service_instance)

    async def delete_vm(self, vm_ref: str) -> None:
        await asyncio.to_thread(self._delete_vm_sync, vm_ref)

    def _upload_iso_sync(self, local_path: str, remote_name: str, skip_if_exists: bool) -> str:
        service_instance = self._connect_sync()
        try:
            content = service_instance.RetrieveContent()
            datacenter = content.rootFolder.childEntity[0]
            datastore_name = self.host.default_datastore
            remote_path = f"/folder/{remote_name}?dcPath={datacenter.name}&dsName={datastore_name}"
            url = f"https://{self.host.api_endpoint}{remote_path}"
            # pyvmomi's stub exposes the raw Set-Cookie header it received
            # (`vmware_soap_session="..."; Path=/; HttpOnly; Secure; `), not
            # a Cookie-header-ready value, those trailing attributes aren't
            # legal in a request Cookie header. httpx validates that
            # strictly and raises on it, so only the name=value pair itself
            # gets reused here.
            cookie = service_instance._stub.cookie.split(";")[0].strip()
            headers = {"Cookie": cookie}
            # ESXi's IsoBackingInfo.fileName needs the full bracketed
            # datastore path, not just the bare file name, an unqualified
            # name is rejected outright as an "Invalid datastore format".
            datastore_path = f"[{datastore_name}] {remote_name}"
            if skip_if_exists:
                # Callers use this for content keyed by a stable id (the
                # Windows ISO shared by every deployment made from the same
                # ISO asset), not the per-deployment answer-file ISO: if a
                # previous deployment already put the exact same file at
                # this path, re-uploading a multi-gigabyte file again on
                # every single deployment would be needlessly slow.
                # A GET is used rather than HEAD (the datastore HTTP
                # endpoint doesn't reliably support HEAD on every ESXi
                # version) but streamed, so the connection closes as soon
                # as the status is known without downloading the file.
                with httpx.stream("GET", url, headers=headers, verify=self.host.tls_verify) as existing:
                    if existing.status_code == 200:
                        return datastore_path
            with open(local_path, "rb") as fh:
                # Passing the open file directly (not fh.read()) lets httpx
                # stream it in chunks instead of loading the whole ISO into
                # memory first.
                httpx.put(url, content=fh, headers=headers, verify=self.host.tls_verify)
            return datastore_path
        finally:
            connect.Disconnect(service_instance)

    async def upload_iso_to_datastore(self, local_path: str, remote_name: str, skip_if_exists: bool = False) -> str:
        return await asyncio.to_thread(self._upload_iso_sync, local_path, remote_name, skip_if_exists)

    def _delete_iso_sync(self, remote_path: str) -> None:
        """remote_path is already the full bracketed datastore path
        (`[datastore] name.iso`), as returned by upload_iso_to_datastore,
        not just a bare file name."""
        service_instance = self._connect_sync()
        try:
            content = service_instance.RetrieveContent()
            datacenter = content.rootFolder.childEntity[0]
            task = content.fileManager.DeleteDatastoreFile_Task(name=remote_path, datacenter=datacenter)
            WaitForTask(task)
        finally:
            connect.Disconnect(service_instance)

    async def delete_iso_from_datastore(self, remote_path: str) -> None:
        await asyncio.to_thread(self._delete_iso_sync, remote_path)
