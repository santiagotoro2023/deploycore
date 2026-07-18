import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.jobs import get_arq_pool
from app.models.m365_config import M365Config
from app.models.notification import NotificationTemplate
from app.models.setting import Setting, SettingScope
from app.models.teams_config import TeamsConfig
from app.models.user import Role, User
from app.schemas.m365 import M365ConfigRead, M365ConfigUpdate
from app.schemas.notification import NotificationTemplateRead, NotificationTemplateUpdate
from app.schemas.setting import SettingRead, SettingValue
from app.schemas.teams import TeamsConfigRead, TeamsConfigUpdate
from app.schemas.update import UpdateStatusRead
from app.security.rbac import get_current_user, require_role
from app.services import audit, m365, remote_session, teams, tls_certs
from app.services.notifications import EVENT_CONTEXT_FIELDS

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


async def _get_teams_config(db: AsyncSession) -> TeamsConfig | None:
    result = await db.execute(select(TeamsConfig).limit(1))
    return result.scalar_one_or_none()


@router.get(
    "/api/settings/global/teams",
    response_model=TeamsConfigRead,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def get_teams_config(db: AsyncSession = Depends(get_db)) -> TeamsConfigRead:
    config = await _get_teams_config(db)
    if config is None:
        return TeamsConfigRead(tenant_id="", client_id="", teams_app_id="", enabled=False, configured=False)
    return TeamsConfigRead(
        tenant_id=config.tenant_id, client_id=config.client_id, teams_app_id=config.teams_app_id,
        enabled=config.enabled, configured=True,
    )


@router.put(
    "/api/settings/global/teams",
    response_model=TeamsConfigRead,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def set_teams_config(
    body: TeamsConfigUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> TeamsConfigRead:
    config = await _get_teams_config(db)
    if config is None:
        if not body.client_secret:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "client_secret is required for initial setup")
        config = TeamsConfig(
            tenant_id=body.tenant_id, client_id=body.client_id, teams_app_id=body.teams_app_id, enabled=body.enabled,
        )
        config.client_secret = body.client_secret
        db.add(config)
    else:
        config.tenant_id = body.tenant_id
        config.client_id = body.client_id
        config.teams_app_id = body.teams_app_id
        config.enabled = body.enabled
        if body.client_secret:
            config.client_secret = body.client_secret
    audit.record(db, action="settings.teams_set", target_type="settings", user_id=current_user.id)
    await db.commit()
    await db.refresh(config)
    return TeamsConfigRead(
        tenant_id=config.tenant_id, client_id=config.client_id, teams_app_id=config.teams_app_id,
        enabled=config.enabled, configured=True,
    )


@router.post(
    "/api/settings/global/teams/test",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def test_teams_config(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict:
    if not current_user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "your account has no email address to use as a Teams UPN")
    config = await _get_teams_config(db)
    if config is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Teams is not configured yet")
    try:
        await teams.send_activity_notification(
            tenant_id=config.tenant_id, client_id=config.client_id, client_secret=config.client_secret,
            teams_app_id=config.teams_app_id, to_upn=current_user.email,
            message="This is a test notification from DeployCore.",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin testing the integration
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"Test notification sent to {current_user.email}"}


@router.get(
    "/api/settings/global/notification-templates",
    response_model=list[NotificationTemplateRead],
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def list_notification_templates(db: AsyncSession = Depends(get_db)) -> list[NotificationTemplate]:
    result = await db.execute(select(NotificationTemplate).order_by(NotificationTemplate.event_type))
    return list(result.scalars().all())


@router.get(
    "/api/settings/global/notification-templates/fields",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def get_notification_template_fields() -> dict:
    """Which {placeholder} keys are actually available per event type,
    for the settings UI's own cheat-sheet next to each editor - see
    services/notifications.py's EVENT_CONTEXT_FIELDS, the single source
    of truth this just exposes read-only."""
    return EVENT_CONTEXT_FIELDS


@router.put(
    "/api/settings/global/notification-templates/{event_type}",
    response_model=NotificationTemplateRead,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def update_notification_template(
    event_type: str,
    body: NotificationTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationTemplate:
    result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.event_type == event_type))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown notification event type")
    template.email_subject = body.email_subject
    template.email_body = body.email_body
    template.teams_message = body.teams_message
    audit.record(
        db, action="settings.notification_template_set", target_type="settings",
        user_id=current_user.id, detail={"event_type": event_type},
    )
    await db.commit()
    await db.refresh(template)
    return template


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
    dependencies=[Depends(require_role(Role.ADMIN))],
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
    pending_changelog = await _get_setting_value(db, "pending_changelog")
    last_update_changelog = await _get_setting_value(db, "last_update_changelog")
    return UpdateStatusRead(
        git_available=git_available,
        current_commit=current_commit,
        latest_commit=latest_commit,
        commits_behind=commits_behind if isinstance(commits_behind, int) else 0,
        checked_at=checked_at,
        stage=update_status.get("stage", "idle"),
        error=update_status.get("error"),
        pending_changelog=pending_changelog if isinstance(pending_changelog, list) else [],
        last_update_changelog=last_update_changelog if isinstance(last_update_changelog, list) else [],
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


@router.post(
    "/api/settings/global/update/check",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def run_update_check(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    """Same request/poll handoff as run_update above, just for a plain
    `git fetch` + recomputing commits_behind, not an actual update: the
    updater container otherwise only refreshes that on its own every 5
    minutes (see updater/update.sh CHECK_INTERVAL), too slow for someone
    sitting on this page wanting to know right now."""
    await _set_global_setting_value(db, "check_requested", True)
    audit.record(db, action="settings.update_check_triggered", target_type="settings", user_id=current_user.id)
    await db.commit()


class RemoteManagementConfigUpdate(BaseModel):
    host: str
    app_public_url: str | None = None


def _sanitize_host(raw: str) -> str:
    """Accept whatever the user pastes (a domain, an IP, or a full URL) and
    reduce it to a bare host - no scheme, path, or port."""
    h = raw.strip()
    h = re.sub(r"^[a-zA-Z]+://", "", h)
    h = h.split("/")[0]
    h = h.split(":")[0]
    return h.strip()


@router.get(
    "/api/settings/remote-management",
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def get_remote_management_config(db: AsyncSession = Depends(get_db)) -> dict:
    """Current Remote Management public host (what the coturn TURN fallback
    and the browser connect to) and the ports to forward for non-LAN access.
    The host defaults to this instance's LAN IP so same-network Remote
    Management works with nothing set. What agents/sessions are TOLD to
    connect to updates immediately (a live DB read); coturn's own
    --external-ip catches up within a couple of seconds via the updater (see
    the PUT route's own docstring) - fast enough that this page doesn't
    surface a separate "applying" state for it."""
    return {
        "host": await remote_session.resolve_public_host(db),
        "app_public_url": await remote_session.resolve_app_public_url(db),
        "ports": remote_session.RELAY_PORTS,
    }


@router.put(
    "/api/settings/remote-management",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def set_remote_management_config(
    body: RemoteManagementConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Sets the public host. Takes effect IMMEDIATELY for every new agent
    enrollment and session (a live DB read) - but coturn's own --external-ip
    (see coturn/entrypoint.sh) only knows TURN_HOST from .env at ITS OWN
    container start, since a container can't read this app's Postgres
    settings table directly. So this also flags the updater to rewrite
    TURN_HOST in .env and recreate just that one lightweight, stateless
    container - unlike the old RustDesk relay stack this replaced, coturn
    has no slow first-run init to wait on, so this resolves within a couple
    of seconds with no visible "Applying..." step needed for it."""
    host = _sanitize_host(body.host)
    if not host:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a host or domain is required")
    await _set_global_setting_value(db, "remote_management_host", host)
    await _set_global_setting_value(db, "remote_management_apply_requested", True)

    # Separate from the TURN host above: this is the address agents use to
    # reach DeployCore's OWN api (enroll/config), previously only settable
    # via the APP_PUBLIC_URL env var at first-run setup.sh time with no way
    # to fix it later short of SSHing in - confirmed live as a real gap,
    # back when this app still ran on RustDesk.
    app_public_url = (body.app_public_url or "").strip().rstrip("/")
    if app_public_url:
        if not re.match(r"^https?://", app_public_url):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "the instance URL must start with http:// or https://")
        await _set_global_setting_value(db, remote_session.APP_PUBLIC_URL_SETTING_KEY, app_public_url)
    else:
        await _set_global_setting_value(db, remote_session.APP_PUBLIC_URL_SETTING_KEY, None)

    audit.record(
        db, action="settings.remote_management_updated", target_type="settings",
        user_id=current_user.id, detail={"host": host, "app_public_url": app_public_url or None},
    )
    await db.commit()
