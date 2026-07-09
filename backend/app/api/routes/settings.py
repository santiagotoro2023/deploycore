import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.jobs import get_arq_pool
from app.models.m365_config import M365Config
from app.models.setting import Setting, SettingScope
from app.models.user import Role, User
from app.schemas.m365 import M365ConfigRead, M365ConfigUpdate
from app.schemas.setting import SettingRead, SettingValue
from app.schemas.update import UpdateStatusRead
from app.security.rbac import get_current_user, require_role
from app.services import audit, m365, tls_certs

router = APIRouter(tags=["settings"])

UPDATE_IN_PROGRESS_STAGES = {"pulling", "building", "restarting", "finalizing"}

DEFAULT_INSTANCE_NAME = "DeployCore"
LOGO_MAX_BYTES = 5 * 1024 * 1024
LOGO_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}


def _backup_dir() -> Path:
    path = Path(get_settings().backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _branding_dir() -> Path:
    path = Path(get_settings().iso_storage_path) / "branding"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tls_dir() -> Path:
    path = Path(get_settings().tls_certs_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _get_setting_value(db: AsyncSession, key: str):
    result = await db.execute(select(Setting.value).where(Setting.scope == SettingScope.GLOBAL, Setting.key == key))
    return result.scalar_one_or_none()


async def _set_global_setting_value(db: AsyncSession, key: str, value) -> None:
    result = await db.execute(select(Setting).where(Setting.scope == SettingScope.GLOBAL, Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        db.add(Setting(scope=SettingScope.GLOBAL, key=key, value=value))
    else:
        setting.value = value


@router.get("/api/instance")
async def get_instance_info(db: AsyncSession = Depends(get_db)) -> dict:
    """Public, just the MSP's own branding, shown in the sidebar/login
    screen for every user regardless of role, unlike the rest of the
    global-settings surface below."""
    name = await _get_setting_value(db, "instance_name")
    logo_filename = await _get_setting_value(db, "logo_filename")
    has_logo = bool(logo_filename) and (_branding_dir() / logo_filename).exists()
    return {"name": name or DEFAULT_INSTANCE_NAME, "has_logo": has_logo}


@router.get("/api/instance/logo")
async def get_instance_logo(db: AsyncSession = Depends(get_db)) -> FileResponse:
    logo_filename = await _get_setting_value(db, "logo_filename")
    if not logo_filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no logo set")
    path = _branding_dir() / logo_filename
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no logo set")
    content_type = LOGO_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=content_type)


@router.put(
    "/api/settings/global/logo",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def set_instance_logo(
    file: UploadFile, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in LOGO_CONTENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "logo must be a PNG, JPEG, or SVG file")
    content = await file.read()
    if len(content) > LOGO_MAX_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "logo must be under 5 MB")

    branding_dir = _branding_dir()
    for existing in branding_dir.glob("logo.*"):
        existing.unlink()
    (branding_dir / f"logo{ext}").write_bytes(content)

    filename = f"logo{ext}"
    result = await db.execute(select(Setting).where(Setting.scope == SettingScope.GLOBAL, Setting.key == "logo_filename"))
    setting = result.scalar_one_or_none()
    if setting is None:
        db.add(Setting(scope=SettingScope.GLOBAL, key="logo_filename", value=filename))
    else:
        setting.value = filename
    audit.record(db, action="settings.logo_set", target_type="settings", user_id=current_user.id)
    await db.commit()
    return {"logo_filename": filename}


@router.delete(
    "/api/settings/global/logo",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def remove_instance_logo(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    for existing in _branding_dir().glob("logo.*"):
        existing.unlink()
    result = await db.execute(select(Setting).where(Setting.scope == SettingScope.GLOBAL, Setting.key == "logo_filename"))
    setting = result.scalar_one_or_none()
    if setting is not None:
        await db.delete(setting)
        audit.record(db, action="settings.logo_remove", target_type="settings", user_id=current_user.id)
        await db.commit()


async def _get_m365_config(db: AsyncSession) -> M365Config | None:
    result = await db.execute(select(M365Config).limit(1))
    return result.scalar_one_or_none()


@router.get(
    "/api/settings/global/m365",
    response_model=M365ConfigRead,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def get_m365_config(db: AsyncSession = Depends(get_db)) -> M365ConfigRead:
    config = await _get_m365_config(db)
    if config is None:
        return M365ConfigRead(tenant_id="", client_id="", sender_upn="", enabled=False, configured=False)
    return M365ConfigRead(
        tenant_id=config.tenant_id, client_id=config.client_id, sender_upn=config.sender_upn,
        enabled=config.enabled, configured=True,
    )


@router.put(
    "/api/settings/global/m365",
    response_model=M365ConfigRead,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def set_m365_config(
    body: M365ConfigUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> M365ConfigRead:
    config = await _get_m365_config(db)
    if config is None:
        if not body.client_secret:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "client_secret is required for initial setup")
        config = M365Config(
            tenant_id=body.tenant_id, client_id=body.client_id, sender_upn=body.sender_upn, enabled=body.enabled,
        )
        config.client_secret = body.client_secret
        db.add(config)
    else:
        config.tenant_id = body.tenant_id
        config.client_id = body.client_id
        config.sender_upn = body.sender_upn
        config.enabled = body.enabled
        if body.client_secret:
            config.client_secret = body.client_secret
    audit.record(db, action="settings.m365_set", target_type="settings", user_id=current_user.id)
    await db.commit()
    await db.refresh(config)
    return M365ConfigRead(
        tenant_id=config.tenant_id, client_id=config.client_id, sender_upn=config.sender_upn,
        enabled=config.enabled, configured=True,
    )


@router.post(
    "/api/settings/global/m365/test",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def test_m365_config(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict:
    if not current_user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "your account has no email address to send a test to")
    config = await _get_m365_config(db)
    if config is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "M365 is not configured yet")
    try:
        await m365.send_mail(
            tenant_id=config.tenant_id, client_id=config.client_id, client_secret=config.client_secret,
            sender_upn=config.sender_upn, to_email=current_user.email,
            subject="DeployCore test email", body="This is a test email from DeployCore.",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin testing the integration
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"Test email sent to {current_user.email}"}


def _tls_status(db_mode: str | None) -> dict:
    uploaded = tls_certs.read_uploaded_info(_tls_dir())
    return {
        "mode": db_mode or "self_signed",
        "has_uploaded_certificate": uploaded is not None,
        "uploaded_subject": uploaded.subject if uploaded else None,
        "uploaded_expires_at": uploaded.not_valid_after.isoformat() if uploaded else None,
    }


@router.get(
    "/api/settings/global/tls",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def get_tls_settings(db: AsyncSession = Depends(get_db)) -> dict:
    """The proxy container (see proxy/entrypoint.sh) polls this same
    `tls_mode` setting straight from Postgres to decide which certificate
    to serve, this endpoint is only for the Settings page to show the
    current state, it doesn't talk to the proxy directly."""
    return _tls_status(await _get_setting_value(db, "tls_mode"))


@router.put(
    "/api/settings/global/tls/certificate",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def upload_tls_certificate(
    cert_file: UploadFile,
    key_file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    cert_pem = await cert_file.read()
    key_pem = await key_file.read()
    try:
        tls_certs.validate_pair(cert_pem, key_pem)
    except tls_certs.InvalidCertificate as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    tls_certs.write_pair(_tls_dir(), cert_pem, key_pem)
    await _set_global_setting_value(db, "tls_mode", "uploaded")
    audit.record(db, action="settings.tls_certificate_uploaded", target_type="settings", user_id=current_user.id)
    await db.commit()
    return _tls_status("uploaded")


@router.put(
    "/api/settings/global/tls/mode",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def set_tls_mode(
    body: SettingValue, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict:
    mode = body.value
    if mode not in ("self_signed", "uploaded"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "mode must be 'self_signed' or 'uploaded'")
    if mode == "uploaded" and tls_certs.read_uploaded_info(_tls_dir()) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no certificate has been uploaded yet")

    await _set_global_setting_value(db, "tls_mode", mode)
    audit.record(
        db, action="settings.tls_mode_set", target_type="settings", user_id=current_user.id, detail={"mode": mode}
    )
    await db.commit()
    return _tls_status(mode)


@router.get(
    "/api/organizations/{org_id}/settings",
    response_model=list[SettingRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_org_settings(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[Setting]:
    result = await db.execute(
        select(Setting).where(
            (Setting.scope == SettingScope.ORG) & (Setting.org_id == org_id) | (Setting.scope == SettingScope.GLOBAL)
        )
    )
    return list(result.scalars().all())


@router.put(
    "/api/organizations/{org_id}/settings/{key}",
    response_model=SettingRead,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def set_org_setting(
    org_id: uuid.UUID,
    key: str,
    body: SettingValue,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Setting:
    result = await db.execute(
        select(Setting).where(Setting.scope == SettingScope.ORG, Setting.org_id == org_id, Setting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = Setting(scope=SettingScope.ORG, org_id=org_id, key=key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value
    audit.record(
        db, action="settings.org_set", target_type="settings", org_id=org_id,
        user_id=current_user.id, detail={"key": key},
    )
    await db.commit()
    await db.refresh(setting)
    return setting


@router.get(
    "/api/settings/global",
    response_model=list[SettingRead],
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def list_global_settings(db: AsyncSession = Depends(get_db)) -> list[Setting]:
    result = await db.execute(select(Setting).where(Setting.scope == SettingScope.GLOBAL))
    return list(result.scalars().all())


@router.put(
    "/api/settings/global/{key}",
    response_model=SettingRead,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def set_global_setting(
    key: str,
    body: SettingValue,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Setting:
    result = await db.execute(select(Setting).where(Setting.scope == SettingScope.GLOBAL, Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = Setting(scope=SettingScope.GLOBAL, key=key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value
    audit.record(db, action="settings.global_set", target_type="settings", user_id=current_user.id, detail={"key": key})
    await db.commit()
    await db.refresh(setting)
    return setting


@router.get(
    "/api/settings/global/backups",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def list_backups() -> list[dict]:
    files = sorted(_backup_dir().glob("deploycore-*.dump"), key=lambda p: p.name, reverse=True)
    return [
        {"filename": f.name, "size_bytes": f.stat().st_size, "created_at": f.stat().st_mtime}
        for f in files
    ]


@router.post(
    "/api/settings/global/backups/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def run_backup_now(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    pool = await get_arq_pool()
    await pool.enqueue_job("run_scheduled_backup")
    audit.record(db, action="backup.run", target_type="backup", user_id=current_user.id)
    await db.commit()


@router.get(
    "/api/settings/global/backups/{filename}",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def download_backup(filename: str) -> FileResponse:
    path = _backup_dir() / Path(filename).name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.get(
    "/api/settings/global/update/status",
    response_model=UpdateStatusRead,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def get_update_status(db: AsyncSession = Depends(get_db)) -> UpdateStatusRead:
    """Reads Setting rows the updater container (updater/update.sh) writes
    directly via psql, never through this API, since api is exactly what
    gets restarted mid-update."""
    git_available = bool(await _get_setting_value(db, "git_available"))
    current_commit = await _get_setting_value(db, "current_commit")
    latest_commit = await _get_setting_value(db, "latest_commit")
    commits_behind = await _get_setting_value(db, "commits_behind")
    checked_at = await _get_setting_value(db, "checked_at")
    update_status = await _get_setting_value(db, "update_status") or {}
    return UpdateStatusRead(
        git_available=git_available,
        current_commit=current_commit,
        latest_commit=latest_commit,
        commits_behind=commits_behind if isinstance(commits_behind, int) else 0,
        checked_at=checked_at,
        stage=update_status.get("stage", "idle"),
        error=update_status.get("error"),
    )


@router.post(
    "/api/settings/global/update/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def run_update(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    update_status = await _get_setting_value(db, "update_status") or {}
    if update_status.get("stage") in UPDATE_IN_PROGRESS_STAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "an update is already in progress")

    result = await db.execute(select(Setting).where(Setting.scope == SettingScope.GLOBAL, Setting.key == "update_requested"))
    setting = result.scalar_one_or_none()
    if setting is None:
        db.add(Setting(scope=SettingScope.GLOBAL, key="update_requested", value=True))
    else:
        setting.value = True
    audit.record(db, action="settings.update_triggered", target_type="settings", user_id=current_user.id)
    await db.commit()
