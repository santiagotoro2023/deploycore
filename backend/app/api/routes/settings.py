import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.setting import Setting, SettingScope
from app.models.user import Role
from app.schemas.setting import SettingRead, SettingValue
from app.security.rbac import require_role

router = APIRouter(tags=["settings"])

DEFAULT_INSTANCE_NAME = "DeployCore"


@router.get("/api/instance")
async def get_instance_info(db: AsyncSession = Depends(get_db)) -> dict:
    """Public — just the MSP's own branding, shown in the sidebar/login
    screen for every user regardless of role, unlike the rest of the
    global-settings surface below."""
    result = await db.execute(
        select(Setting.value).where(Setting.scope == SettingScope.GLOBAL, Setting.key == "instance_name")
    )
    name = result.scalar_one_or_none()
    return {"name": name or DEFAULT_INSTANCE_NAME}


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
    org_id: uuid.UUID, key: str, body: SettingValue, db: AsyncSession = Depends(get_db)
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
async def set_global_setting(key: str, body: SettingValue, db: AsyncSession = Depends(get_db)) -> Setting:
    result = await db.execute(select(Setting).where(Setting.scope == SettingScope.GLOBAL, Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = Setting(scope=SettingScope.GLOBAL, key=key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value
    await db.commit()
    await db.refresh(setting)
    return setting
