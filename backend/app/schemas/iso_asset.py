import uuid

from pydantic import BaseModel, ConfigDict

from app.models.iso_asset import IsoKind, UploadStatus


class IsoAssetCreate(BaseModel):
    filename: str
    kind: IsoKind


class IsoAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    kind: IsoKind
    filename: str
    checksum_sha256: str | None
    size_bytes: int
    upload_status: UploadStatus
