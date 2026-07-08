import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.m365_config import M365Config
from app.models.notification import Notification, NotificationPreference
from app.models.user import User


def notify(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    message: str,
    deployment_id: uuid.UUID | None = None,
) -> None:
    """Adds to the session without committing, same convention as
    services.audit.record: callers fold this into whatever transaction is
    already writing the event being notified about."""
    db.add(Notification(user_id=user_id, deployment_id=deployment_id, message=message))


_PREFERENCE_DEFAULTS = {
    "start": False,
    "complete": True,
    "failed": True,
    "health_degraded": False,
}


async def maybe_email(
    db: AsyncSession,
    pool,
    *,
    user_id: uuid.UUID,
    event_type: str,
    subject: str,
    body: str,
) -> None:
    """Enqueues send_email_notification if, and only if: the user has an
    email address, their preference for this event type is on (defaulting
    to _PREFERENCE_DEFAULTS when they've never visited Account settings),
    and M365 is configured and enabled. `pool` is anything with an
    `enqueue_job` coroutine method: app.jobs.get_arq_pool() in the API
    process, or `ctx["redis"]` inside a worker task."""
    user = await db.get(User, user_id)
    if user is None or not user.email:
        return

    pref_result = await db.execute(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
    pref = pref_result.scalar_one_or_none()
    if pref is not None:
        enabled = getattr(pref, f"email_on_{event_type}")
    else:
        enabled = _PREFERENCE_DEFAULTS.get(event_type, False)
    if not enabled:
        return

    config_result = await db.execute(select(M365Config).limit(1))
    config = config_result.scalar_one_or_none()
    if config is None or not config.enabled:
        return

    await pool.enqueue_job("send_email_notification", user.email, subject, body)
