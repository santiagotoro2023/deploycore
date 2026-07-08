import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import Setting, SettingScope

# Fallback used only when no Setting row exists at any scope.
_DEFAULTS = {"os_install_timeout_minutes": 90}


async def resolve(
    db: AsyncSession,
    key: str,
    *,
    org_id: uuid.UUID | None = None,
    template_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
):
    """Resolution order: deployment -> template -> org -> global, first
    match wins. Falls back to _DEFAULTS (or None) if nothing is set at any
    scope."""
    lookups = []
    if deployment_id is not None:
        lookups.append((SettingScope.DEPLOYMENT, Setting.deployment_id, deployment_id))
    if template_id is not None:
        lookups.append((SettingScope.TEMPLATE, Setting.template_id, template_id))
    if org_id is not None:
        lookups.append((SettingScope.ORG, Setting.org_id, org_id))
    lookups.append((SettingScope.GLOBAL, None, None))

    for scope, column, value in lookups:
        stmt = select(Setting.value).where(Setting.scope == scope, Setting.key == key)
        if column is not None:
            stmt = stmt.where(column == value)
        result = await db.execute(stmt)
        found = result.scalar_one_or_none()
        if found is not None:
            return found
    return _DEFAULTS.get(key)
