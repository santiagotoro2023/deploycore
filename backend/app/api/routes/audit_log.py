import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.audit_log import AuditLog
from app.models.user import Role
from app.schemas.audit_log import AuditLogRead
from app.security.rbac import require_role

router = APIRouter(tags=["audit-log"])


@router.get(
    "/api/organizations/{org_id}/audit-log",
    response_model=list[AuditLogRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_audit_log(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[AuditLog]:
    result = await db.execute(
        select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.occurred_at.desc()).limit(200)
    )
    return list(result.scalars().all())
