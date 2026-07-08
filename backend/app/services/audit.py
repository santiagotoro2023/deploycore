import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


def record(
    db: AsyncSession,
    *,
    action: str,
    target_type: str,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> None:
    """Adds to the session without committing — callers fold this into
    whatever transaction is already writing the mutation being audited."""
    db.add(
        AuditLog(
            org_id=org_id,
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )
