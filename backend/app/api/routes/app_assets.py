import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.app_asset import AppAsset
from app.models.deployment import Deployment
from app.models.iso_asset import UploadStatus
from app.models.user import Role, User
from app.schemas.app_asset import AppAssetCreate, AppAssetRead, AppAssetSetRemoteAgent, AppAssetUpdate
from app.security.rbac import get_current_user, require_role
from app.services import app_asset_upload, audit, remote_agent_seed

router = APIRouter(tags=["app-assets"])

_admin_global = Depends(require_role(Role.ADMIN, org_scoped=False))


@router.get(
    "/api/organizations/{org_id}/app-assets",
    response_model=list[AppAssetRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_app_assets(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[AppAsset]:
    result = await db.execute(select(AppAsset).where(or_(AppAsset.org_id == org_id, AppAsset.org_id.is_(None))))
    return list(result.scalars().all())


@router.post(
    "/api/organizations/{org_id}/app-assets",
    response_model=AppAssetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def create_app_asset(
    org_id: uuid.UUID,
    body: AppAssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AppAsset:
    app_asset = AppAsset(
        org_id=org_id, kind=body.kind, name=body.name, filename=body.filename,
        default_install_args=body.default_install_args, storage_path="",
    )
    db.add(app_asset)
    await db.flush()
    audit.record(
        db, action="app_asset.create", target_type="app_asset", org_id=org_id,
        user_id=current_user.id, target_id=app_asset.id, detail={"name": app_asset.name},
    )
    await db.commit()
    await db.refresh(app_asset)
    return app_asset


async def _get_org_owned_app_asset(db: AsyncSession, org_id: uuid.UUID, app_id: uuid.UUID) -> AppAsset:
    app_asset = await db.get(AppAsset, app_id)
    if app_asset is None or app_asset.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app asset not found in this organization")
    return app_asset


@router.post(
    "/api/organizations/{org_id}/app-assets/{app_id}/chunk",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def upload_app_asset_chunk(
    org_id: uuid.UUID, app_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
) -> None:
    app_asset = await _get_org_owned_app_asset(db, org_id, app_id)
    chunk = await request.body()
    app_asset_upload.append_chunk(app_asset.id, chunk)
    if app_asset.upload_status != UploadStatus.UPLOADING:
        app_asset.upload_status = UploadStatus.UPLOADING
        await db.commit()


@router.post(
    "/api/organizations/{org_id}/app-assets/{app_id}/finalize",
    response_model=AppAssetRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def finalize_app_asset_upload(
    org_id: uuid.UUID,
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AppAsset:
    app_asset = await _get_org_owned_app_asset(db, org_id, app_id)
    try:
        storage_path, checksum, size_bytes = app_asset_upload.finalize(app_asset.id, app_asset.filename)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no chunks were uploaded for this app asset")
    app_asset.storage_path = storage_path
    app_asset.checksum_sha256 = checksum
    app_asset.size_bytes = size_bytes
    app_asset.upload_status = UploadStatus.COMPLETE
    audit.record(
        db, action="app_asset.finalize", target_type="app_asset", org_id=org_id,
        user_id=current_user.id, target_id=app_asset.id, detail={"size_bytes": size_bytes},
    )
    await db.commit()
    await db.refresh(app_asset)
    return app_asset


@router.patch(
    "/api/organizations/{org_id}/app-assets/{app_id}",
    response_model=AppAssetRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def update_app_asset(
    org_id: uuid.UUID,
    app_id: uuid.UUID,
    body: AppAssetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AppAsset:
    """Metadata only (name/kind/default_install_args) - the uploaded file
    itself is immutable, re-upload as a new asset to replace it. Doesn't
    touch org_id: an asset's org-vs-global scope is fixed at creation,
    matching how templates and ISOs both work, converting scope after
    the fact isn't a thing this codebase supports for any asset type."""
    app_asset = await _get_org_owned_app_asset(db, org_id, app_id)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(app_asset, field, value)
    audit.record(
        db, action="app_asset.update", target_type="app_asset", org_id=org_id,
        user_id=current_user.id, target_id=app_asset.id, detail=updates,
    )
    await db.commit()
    await db.refresh(app_asset)
    return app_asset


@router.delete(
    "/api/organizations/{org_id}/app-assets/{app_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def delete_app_asset(
    org_id: uuid.UUID,
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    app_asset = await _get_org_owned_app_asset(db, org_id, app_id)
    storage_path = app_asset.storage_path
    audit.record(
        db, action="app_asset.delete", target_type="app_asset", org_id=org_id,
        user_id=current_user.id, target_id=app_asset.id, detail={"name": app_asset.name},
    )
    await db.delete(app_asset)
    # commit before touching the file, same reasoning as iso_assets.py: a
    # template's app_installs list just references this id by string
    # inside a JSONB blob, nothing DB-level blocks the delete, but if the
    # commit ever fails for some other reason the file must stay in sync
    # with the row that still claims it exists.
    await db.commit()
    if storage_path:
        Path(storage_path).unlink(missing_ok=True)


async def _get_global_app_asset(db: AsyncSession, app_id: uuid.UUID) -> AppAsset:
    app_asset = await db.get(AppAsset, app_id)
    if app_asset is None or app_asset.org_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "global app asset not found")
    return app_asset


@router.post(
    "/api/app-assets/global",
    response_model=AppAssetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_global],
)
async def create_global_app_asset(
    body: AppAssetCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> AppAsset:
    app_asset = AppAsset(
        org_id=None, kind=body.kind, name=body.name, filename=body.filename,
        default_install_args=body.default_install_args, storage_path="",
    )
    db.add(app_asset)
    await db.flush()
    audit.record(
        db, action="app_asset.create_global", target_type="app_asset",
        user_id=current_user.id, target_id=app_asset.id, detail={"name": app_asset.name},
    )
    await db.commit()
    await db.refresh(app_asset)
    return app_asset


@router.post(
    "/api/app-assets/global/{app_id}/chunk",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_admin_global],
)
async def upload_global_app_asset_chunk(app_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)) -> None:
    app_asset = await _get_global_app_asset(db, app_id)
    chunk = await request.body()
    app_asset_upload.append_chunk(app_asset.id, chunk)
    if app_asset.upload_status != UploadStatus.UPLOADING:
        app_asset.upload_status = UploadStatus.UPLOADING
        await db.commit()


@router.post(
    "/api/app-assets/global/{app_id}/finalize",
    response_model=AppAssetRead,
    dependencies=[_admin_global],
)
async def finalize_global_app_asset_upload(
    app_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> AppAsset:
    app_asset = await _get_global_app_asset(db, app_id)
    try:
        storage_path, checksum, size_bytes = app_asset_upload.finalize(app_asset.id, app_asset.filename)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no chunks were uploaded for this app asset")
    app_asset.storage_path = storage_path
    app_asset.checksum_sha256 = checksum
    app_asset.size_bytes = size_bytes
    app_asset.upload_status = UploadStatus.COMPLETE
    audit.record(
        db, action="app_asset.finalize_global", target_type="app_asset",
        user_id=current_user.id, target_id=app_asset.id, detail={"size_bytes": size_bytes},
    )
    await db.commit()
    await db.refresh(app_asset)
    return app_asset


@router.patch(
    "/api/app-assets/global/{app_id}",
    response_model=AppAssetRead,
    dependencies=[_admin_global],
)
async def update_global_app_asset(
    app_id: uuid.UUID, body: AppAssetUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> AppAsset:
    app_asset = await _get_global_app_asset(db, app_id)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(app_asset, field, value)
    audit.record(
        db, action="app_asset.update_global", target_type="app_asset",
        user_id=current_user.id, target_id=app_asset.id, detail=updates,
    )
    await db.commit()
    await db.refresh(app_asset)
    return app_asset


@router.post(
    "/api/app-assets/global/{app_id}/set-remote-agent",
    response_model=AppAssetRead,
    dependencies=[_admin_global],
)
async def set_remote_agent_app_asset(
    app_id: uuid.UUID, body: AppAssetSetRemoteAgent, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AppAsset:
    """Designates (or un-designates) this global asset as THE DeployCore
    Remote Management Agent installer - the one file managed_hosts.py's
    agent-installer download route and worker/tasks/provision.py's
    per-deployment enrollment injection both look for via
    AppAsset.is_remote_agent (see its own field docstring). Only one asset
    can hold this at a time: enabling it here clears it from every other
    asset first, rather than leaving multiple candidates for those two
    call sites to pick between.

    Uploading a real, working agent installer isn't something this route
    does - that installer (stock RustDesk configured headless + a small
    DeployCore-branded tray companion + the enrollment glue script, see
    remote_management_architecture project notes) has to be built and
    uploaded through the normal global-app-asset upload flow first, same
    as any other app asset; this route only flips the designation once
    that upload is sitting there complete."""
    app_asset = await _get_global_app_asset(db, app_id)
    if body.enabled and app_asset.upload_status != UploadStatus.COMPLETE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "upload must be complete before this can be the remote agent")
    if body.enabled:
        await db.execute(
            AppAsset.__table__.update().where(AppAsset.is_remote_agent.is_(True)).values(is_remote_agent=False)
        )
    app_asset.is_remote_agent = body.enabled
    audit.record(
        db, action="app_asset.set_remote_agent", target_type="app_asset",
        user_id=current_user.id, target_id=app_asset.id, detail={"enabled": body.enabled},
    )
    await db.commit()
    await db.refresh(app_asset)
    return app_asset


@router.post(
    "/api/app-assets/global/refresh-agent",
    response_model=AppAssetRead,
    dependencies=[_admin_global],
)
async def refresh_remote_agent_asset(db: AsyncSession = Depends(get_db)) -> AppAsset:
    """Forces an immediate re-check of the agent .msi's own GitHub release,
    on demand, rather than waiting for the api container's next restart (the
    only other time this normally runs - see remote_agent_seed.py's own
    lifespan-hook wiring in main.py). Exists specifically because "did the
    api container actually pick up the newest build yet" was confirmed live
    to be a real, recurring source of confusion during active iteration on
    the agent script - a rebuild-and-restart's own timing relative to a CI
    publish is otherwise invisible and easy to get wrong. Read-only from the
    caller's point of view if nothing changed - but unlike main.py's own
    startup call, this one passes force=True: confirmed live that two
    genuinely different CI builds can land at the exact same byte size (a
    version-string change that didn't happen to shift the MSI's padded
    size), which the cheap size-only check main.py uses at startup can't
    tell apart. A user explicitly clicking "check for update" needs a real
    answer, not a coin-flip - so this always re-downloads and re-compares,
    trading a few seconds and 22MB of bandwidth for certainty."""
    await remote_agent_seed.ensure_agent_asset_seeded(db, force=True)
    result = await db.execute(select(AppAsset).where(AppAsset.is_remote_agent.is_(True)))
    asset = result.scalars().first()
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no agent installer is available yet - check the remote_agent_msi_url setting and that the CI build has published a release")
    return asset


@router.delete(
    "/api/app-assets/global/{app_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_admin_global],
)
async def delete_global_app_asset(
    app_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    app_asset = await _get_global_app_asset(db, app_id)
    storage_path = app_asset.storage_path
    audit.record(
        db, action="app_asset.delete_global", target_type="app_asset",
        user_id=current_user.id, target_id=app_asset.id, detail={"name": app_asset.name},
    )
    await db.delete(app_asset)
    await db.commit()
    if storage_path:
        Path(storage_path).unlink(missing_ok=True)


@router.get("/api/deployments/{deployment_id}/app-assets/{app_id}/download")
async def download_app_asset(deployment_id: uuid.UUID, app_id: uuid.UUID, token: str, db: AsyncSession = Depends(get_db)):
    """Authenticated by a per-deployment token (deployment.app_asset_access_token,
    generated right before app installs start and cleared right after, see
    worker/tasks/provision.py), not a user session: the caller is the guest
    VM's own Invoke-WebRequest during post_install, same reasoning as
    /api/callback/{token} authenticating the Setup-complete callback."""
    deployment = await db.get(Deployment, deployment_id)
    if deployment is None or not deployment.app_asset_access_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if not secrets.compare_digest(deployment.app_asset_access_token, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid token")

    app_asset = await db.get(AppAsset, app_id)
    if app_asset is None or (app_asset.org_id is not None and app_asset.org_id != deployment.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app asset not found")
    if not app_asset.storage_path or not Path(app_asset.storage_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app asset file missing")

    return FileResponse(app_asset.storage_path, filename=app_asset.filename, media_type="application/octet-stream")
