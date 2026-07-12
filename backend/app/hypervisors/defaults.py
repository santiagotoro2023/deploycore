import secrets

HYPERVISOR_DEFAULTS = {
    "esxi": {
        "firmware": "efi",
        # LSI Logic SAS, not PVSCSI: Windows has no inbox driver for
        # VMware's own paravirtualized SCSI controller, a boot disk on it
        # can't be recognized during Setup or on later boots without
        # injecting the VMware Tools PVSCSI driver first, which nothing in
        # this pipeline does. LSI Logic SAS needs no driver injection, see
        # hypervisors/esxi.py's VM creation for the actual controller
        # class (this string isn't read there, kept in sync as
        # descriptive metadata, requires_driver_injection below is what
        # actually gates provision.py's VirtIO-ISO-attach logic).
        "scsi_controller": "lsilogicsas",
        "requires_driver_injection": False,
        # VMware's own documented range reserved for administrator-
        # assigned MAC addresses (00:50:56:00:00:00-00:50:56:3F:FF:FF, the
        # 4th octet's top two bits clear), specifically so a manually set
        # address can never collide with one ESXi auto-generates itself
        # (00:50:56:40:00:00 and up, or other OUIs depending on version).
        "mac_oui": "00:50:56",
        "mac_fourth_octet_max": 0x40,
    },
}


def generate_mac_address(hypervisor_type: str) -> str:
    """Explicitly assigned per deployment (not left to the hypervisor to
    auto-generate) so the exact same value can be baked into the answer
    file's static-network Identifier before the VM even exists to report
    one back - see _static_network.xml.j2's comment for why matching by
    interface alias ("Ethernet") wasn't reliable enough to keep using:
    real deployments ended up with the static config silently never
    applied (Setup didn't error, the adapter just stayed on its DHCP
    default), consistent with Microsoft's own documented caveat that
    interface alias/LUID matching "is not guaranteed to be the same
    between different builds." A MAC address is the deterministic
    alternative Microsoft's own component reference documents Identifier
    as accepting."""
    defaults = HYPERVISOR_DEFAULTS[hypervisor_type]
    fourth_octet = secrets.randbelow(defaults["mac_fourth_octet_max"])
    remaining = [fourth_octet, secrets.randbelow(0x100), secrets.randbelow(0x100)]
    return defaults["mac_oui"] + ":" + ":".join(f"{b:02x}" for b in remaining)
