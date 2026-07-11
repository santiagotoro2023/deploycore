import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.m365_config import M365Config
from app.models.notification import Notification, NotificationPreference, NotificationTemplate
from app.models.teams_config import TeamsConfig
from app.models.user import User

# Which {placeholder} keys each event type's context dict actually
# provides - the source of truth for what an operator can reference when
# editing a notification template (see settings routes/UI), and what
# _render below leaves untouched (literal "{typo}") rather than crashing
# on if a template references something that isn't there.
EVENT_CONTEXT_FIELDS = {
    "start": ["hostname"],
    "complete": ["hostname"],
    "failed": ["hostname", "error"],
    "health_degraded": ["hostname", "checked_at"],
}


def notify(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    message: str,
    deployment_id: uuid.UUID | None = None,
) -> None:
    """Adds to the session without committing, same convention as
    services.audit.record: callers fold this into whatever transaction is
    already writing the event being notified about. user_id is optional
    because a deployment's creator (Deployment.created_by_user_id) can be
    None if that user was since deleted, nothing to notify then."""
    if user_id is None:
        return
    db.add(Notification(user_id=user_id, deployment_id=deployment_id, message=message))


_PREFERENCE_DEFAULTS = {
    "start": False,
    "complete": True,
    "failed": True,
    "health_degraded": False,
}


class _SafeDict(dict):
    """str.format_map with this leaves an unknown {placeholder} exactly
    as typed instead of raising KeyError - an operator's template typo,
    or a placeholder that's valid for a different event type, should
    never be able to block a real notification from going out."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render(text: str, context: dict) -> str:
    return text.format_map(_SafeDict(context))


async def _get_template(db: AsyncSession, event_type: str) -> NotificationTemplate | None:
    result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.event_type == event_type))
    return result.scalar_one_or_none()


async def dispatch(
    db: AsyncSession,
    pool,
    *,
    user_id: uuid.UUID | None,
    event_type: str,
    context: dict,
) -> None:
    """Renders and enqueues both the email and Teams deliveries for one
    event, each independently gated on that user's own preference for
    this event type/channel and on the corresponding integration being
    configured and enabled instance-wide. `pool` is anything with an
    `enqueue_job` coroutine method: app.jobs.get_arq_pool() in the API
    process, or `ctx["redis"]` inside a worker task. user_id is optional,
    see notify()'s docstring."""
    if user_id is None:
        return
    user = await db.get(User, user_id)
    if user is None or not user.email:
        return

    template = await _get_template(db, event_type)
    if template is None:
        return  # every event type is seeded by migration 0032; a missing row means nothing to send, not an error

    pref_result = await db.execute(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
    pref = pref_result.scalar_one_or_none()

    def _enabled(channel: str) -> bool:
        attr = f"{channel}_on_{event_type}"
        return getattr(pref, attr) if pref is not None else _PREFERENCE_DEFAULTS.get(event_type, False)

    if _enabled("email"):
        m365_result = await db.execute(select(M365Config).limit(1))
        m365_config = m365_result.scalar_one_or_none()
        if m365_config is not None and m365_config.enabled:
            await pool.enqueue_job(
                "send_email_notification", user.email,
                _render(template.email_subject, context), _render(template.email_body, context),
            )

    if _enabled("teams"):
        teams_result = await db.execute(select(TeamsConfig).limit(1))
        teams_config = teams_result.scalar_one_or_none()
        if teams_config is not None and teams_config.enabled:
            await pool.enqueue_job(
                "send_teams_notification", user.email, _render(template.teams_message, context),
            )
