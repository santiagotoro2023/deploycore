HYPERVISOR_DEFAULTS = {
    "esxi": {
        "firmware": "efi",
        "scsi_controller": "pvscsi",
        "requires_driver_injection": False,
    },
    "proxmox": {
        "firmware": "efi",
        "scsi_controller": "virtio-scsi",
        # VirtIO SCSI isn't a Windows Server 2025 in-box driver — the
        # autounattend build must attach a VirtIO driver ISO and load it
        # during the WindowsPE pass, or setup won't see the disk at all.
        "requires_driver_injection": True,
    },
}
