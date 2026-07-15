import uuid

from pydantic import BaseModel, ConfigDict

from app.models.app_asset import AppKind
from app.models.iso_asset import UploadStatus


class AppAssetCreate(BaseModel):
    name: str
    filename: str
    kind: AppKind
    default_install_args: str = ""


class AppAssetUpdate(BaseModel):
    name: str | None = None
    kind: AppKind | None = None
    default_install_args: str | None = None


class AppAssetSetRemoteAgent(BaseModel):
    enabled: bool


class AppAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    kind: AppKind
    name: str
    filename: str
    checksum_sha256: str | None
    size_bytes: int
    default_install_args: str
    upload_status: UploadStatus
    # Seed-only, not user-settable via Create/Update - provision.py trusts
    # this flag to special-case the one global agent asset (inject a live
    # per-deployment enroll token into install_args), so it must not be
    # something an uploaded, arbitrary app can just opt itself into.
    is_remote_agent: bool
