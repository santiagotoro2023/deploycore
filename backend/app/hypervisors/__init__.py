from app.hypervisors.base import ConnectionResult, HypervisorDriver, PowerState, VmSpec
from app.hypervisors.esxi import ESXiDriver
from app.models.hypervisor import HypervisorHost, HypervisorType

_DRIVERS: dict[HypervisorType, type[HypervisorDriver]] = {
    HypervisorType.ESXI: ESXiDriver,
}


def get_driver(host: HypervisorHost) -> HypervisorDriver:
    return _DRIVERS[host.type](host)


__all__ = ["ConnectionResult", "HypervisorDriver", "PowerState", "VmSpec", "get_driver"]
