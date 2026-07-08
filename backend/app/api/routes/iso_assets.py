import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.iso_asset import IsoAsset, UploadStatus
from app.models.user import Role, User
from app.schemas.iso_asset import IsoAssetCreate, IsoAssetRead
from app.security.rbac import get_current_user, require_role
from app.services import audit, iso_upload

router = APIRouter(tags=["iso-assets"])


@router.get(
    "/api/organizations/{org_id}/iso-assets",
    response_model=list[IsoAssetRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_iso_assets(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[IsoAsset]:
    result = await db.execute(select(IsoAsset).where(or_(IsoAsset.org_id == org_id, IsoAsset.org_id.is_(None))))
    return list(result.scalars().all())


@router.post(
    "/api/organizations/{org_id}/iso-assets",
    response_model=IsoAssetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def create_iso_asset(
    org_id: uuid.UUID,
    body: IsoAssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IsoAsset:
    """Registers the metadata row an upload will be assembled into. The
    actual bytes arrive via the chunk/finalize endpoints below."""
    iso = IsoAsset(org_id=org_id, kind=body.kind, filename=body.filename, storage_path="")
    db.add(iso)
    await db.flush()
    audit.record(
        db, action="iso_asset.create", target_type="iso_asset", org_id=org_id,
        user_id=current_user.id, target_id=iso.id, detail={"filename": iso.filename},
    )
    await db.commit()
    await db.refresh(iso)
    return iso


async def _get_org_owned_iso(db: AsyncSession, org_id: uuid.UUID, iso_id: uuid.UUID) -> IsoAsset:
    iso = await db.get(IsoAsset, iso_id)
    if iso is None or iso.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ISO asset not found in this organization")
    return iso


@router.post(
    "/api/organizations/{org_id}/iso-assets/{iso_id}/chunk",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def upload_iso_chunk(
    org_id: uuid.UUID, iso_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
) -> None:
    iso = await _get_org_owned_iso(db, org_id, iso_id)
    chunk = await request.body()
    iso_upload.append_chunk(iso.id, chunk)
    if iso.upload_status != UploadStatus.UPLOADING:
        iso.upload_status = UploadStatus.UPLOADING
        await db.commit()


@router.post(
    "/api/organizations/{org_id}/iso-assets/{iso_id}/finalize",
    response_model=IsoAssetRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def finalize_iso_upload(
    org_id: uuid.UUID,
    iso_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IsoAsset:
    iso = await _get_org_owned_iso(db, org_id, iso_id)
    try:
        storage_path, checksum, size_bytes = iso_upload.finalize(iso.id, iso.filename)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no chunks were uploaded for this ISO asset")
    iso.storage_path = storage_path
    iso.checksum_sha256 = checksum
    iso.size_bytes = size_bytes
    iso.upload_status = UploadStatus.COMPLETE
    audit.record(
        db, action="iso_asset.finalize", target_type="iso_asset", org_id=org_id,
        user_id=current_user.id, target_id=iso.id, detail={"size_bytes": size_bytes},
    )
    await db.commit()
    await db.refresh(iso)
    return iso


@router.delete(
    "/api/organizations/{org_id}/iso-assets/{iso_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def delete_iso_asset(
    org_id: uuid.UUID,
    iso_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    iso = await _get_org_owned_iso(db, org_id, iso_id)
    if iso.storage_path:
        Path(iso.storage_path).unlink(missing_ok=True)
    audit.record(
        db, action="iso_asset.delete", target_type="iso_asset", org_id=org_id,
        user_id=current_user.id, target_id=iso.id, detail={"filename": iso.filename},
    )
    await db.delete(iso)
    await db.commit()
