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
    },
    "proxmox": {
        "firmware": "efi",
        "scsi_controller": "virtio-scsi",
        # VirtIO SCSI isn't a Windows Server 2025 in-box driver, the
        # autounattend build must attach a VirtIO driver ISO and load it
        # during the WindowsPE pass, or setup won't see the disk at all.
        "requires_driver_injection": True,
    },
}
