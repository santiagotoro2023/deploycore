import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.notification import Notification, NotificationPreference
from app.models.user import User
from app.schemas.notification import (
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
    UnreadCount,
)
from app.security.rbac import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
prefs_router = APIRouter(prefix="/api/notification-preferences", tags=["notifications"])


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Notification]:
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(result.scalars().all())


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> UnreadCount:
    count = await db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == current_user.id, Notification.read.is_(False)
        )
    )
    return UnreadCount(count=count or 0)


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    notification.read = True
    await db.commit()


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    result = await db.execute(
        select(Notification).where(Notification.user_id == current_user.id, Notification.read.is_(False))
    )
    for notification in result.scalars().all():
        notification.read = True
    await db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_all(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await db.execute(delete(Notification).where(Notification.user_id == current_user.id))
    await db.commit()


async def _get_or_create_preferences(db: AsyncSession, user_id: uuid.UUID) -> NotificationPreference:
    result = await db.execute(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
    pref = result.scalar_one_or_none()
    if pref is None:
        pref = NotificationPreference(user_id=user_id)
        db.add(pref)
        await db.commit()
        await db.refresh(pref)
    return pref


@prefs_router.get("", response_model=NotificationPreferenceRead)
async def get_notification_preferences(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> NotificationPreference:
    return await _get_or_create_preferences(db, current_user.id)


@prefs_router.put("", response_model=NotificationPreferenceRead)
async def set_notification_preferences(
    body: NotificationPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationPreference:
    pref = await _get_or_create_preferences(db, current_user.id)
    pref.email_on_start = body.email_on_start
    pref.email_on_complete = body.email_on_complete
    pref.email_on_failed = body.email_on_failed
    pref.email_on_health_degraded = body.email_on_health_degraded
    pref.teams_on_start = body.teams_on_start
    pref.teams_on_complete = body.teams_on_complete
    pref.teams_on_failed = body.teams_on_failed
    pref.teams_on_health_degraded = body.teams_on_health_degraded
    await db.commit()
    await db.refresh(pref)
    return pref
