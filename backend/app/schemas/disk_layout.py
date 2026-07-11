import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PostInstallScript(BaseModel):
    name: str
    script_text: str


class ExtraVolume(BaseModel):
    label: str
    drive_letter: str
    size_mb: int


class FixedOsVolume(BaseModel):
    size_mb: int


class DiskLayoutJson(BaseModel):
    efi_size_mb: int = 500
    msr_size_mb: int = 128
    # optional Windows RE tools partition placed between MSR and the OS
    # volume instead of at the end of the disk, so later expanding the OS
    # volume in a hypervisor is not blocked by a trailing recovery
    # partition. None omits it entirely (Windows Setup's own default
    # end-of-disk placement applies).
    recovery_size_mb: int | None = None
    os_volume: Literal["remaining"] | FixedOsVolume
    extra_volumes: list[ExtraVolume] = []


class DiskLayoutCreate(BaseModel):
    name: str
    layout: DiskLayoutJson
    post_install_scripts: list[PostInstallScript] = []


class DiskLayoutUpdate(BaseModel):
    name: str | None = None
    layout: DiskLayoutJson | None = None
    post_install_scripts: list[PostInstallScript] | None = None


class DiskLayoutRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    name: str
    layout_json: dict
    post_install_scripts: list[PostInstallScript]
