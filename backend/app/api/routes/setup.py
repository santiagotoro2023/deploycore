from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.disk_layout import DiskLayout
from app.models.setting import Setting, SettingScope
from app.models.user import Role, User
from app.schemas.auth import TokenResponse
from app.schemas.setup import SetupRequest, SetupStatus
from app.security.auth import create_access_token, hash_password
from app.services import audit

router = APIRouter(prefix="/api/setup", tags=["setup"])

# EFI/MSR at Microsoft/diskpart defaults; recovery partition sized per the
# "recovery mid-disk" technique (a Windows RE tools partition placed before
# the OS volume instead of appended at the end, so expanding the OS volume
# later is not blocked by a trailing recovery partition).
DEFAULT_DISK_LAYOUT_NAME = "Windows Server (Recovery Mid-Disk)"
DEFAULT_DISK_LAYOUT_JSON = {
    "efi_size_mb": 100,
    "msr_size_mb": 16,
    "recovery_size_mb": 1000,
    "os_volume": "remaining",
    "extra_volumes": [],
}


async def _needs_setup(db: AsyncSession) -> bool:
    count = await db.scalar(select(func.count()).select_from(User))
    return count == 0


@router.get("/status", response_model=SetupStatus)
async def setup_status(db: AsyncSession = Depends(get_db)) -> SetupStatus:
    return SetupStatus(needs_setup=await _needs_setup(db))


@router.post("", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def complete_setup(body: SetupRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """One-shot instance bootstrap: creates the first (global admin) user
    and the instance name. Refuses once any user already exists, after
    that the instance name is edited from Settings instead."""
    if not await _needs_setup(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "this instance is already set up")

    admin = User(
        username=body.admin_username,
        email=body.admin_email,
        password_hash=hash_password(body.admin_password),
        display_name=body.admin_display_name,
        global_role=Role.ADMIN,
    )
    db.add(admin)
    await db.flush()

    db.add(Setting(scope=SettingScope.GLOBAL, key="instance_name", value=body.instance_name))
    db.add(DiskLayout(org_id=None, name=DEFAULT_DISK_LAYOUT_NAME, layout_json=DEFAULT_DISK_LAYOUT_JSON))
    audit.record(
        db,
        action="instance.setup",
        target_type="instance",
        user_id=admin.id,
        detail={"instance_name": body.instance_name},
    )
    await db.commit()

    return TokenResponse(access_token=create_access_token(admin.id))
