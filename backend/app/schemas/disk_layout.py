import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ExtraVolume(BaseModel):
    label: str
    drive_letter: str
    size_mb: int


class FixedOsVolume(BaseModel):
    size_mb: int


class DiskLayoutJson(BaseModel):
    efi_size_mb: int = 500
    msr_size_mb: int = 128
    os_volume: Literal["remaining"] | FixedOsVolume
    extra_volumes: list[ExtraVolume] = []


class DiskLayoutCreate(BaseModel):
    name: str
    layout: DiskLayoutJson


class DiskLayoutUpdate(BaseModel):
    name: str | None = None
    layout: DiskLayoutJson | None = None


class DiskLayoutRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    name: str
    layout_json: dict
