from sqlalchemy import select

from app.db import SessionLocal
from app.models.m365_config import M365Config
from app.models.teams_config import TeamsConfig
from app.services import m365, teams


async def send_email_notification(ctx, to_email: str, subject: str, body: str) -> None:
    """Delivery only, never blocks or fails the caller: the deployment
    pipeline and the API request that enqueued this have both already
    moved on. A failed send here is logged (arq's own job-failure log) and
    otherwise silently dropped, matching the health-check task's
    best-effort posture."""
    async with SessionLocal() as db:
        result = await db.execute(select(M365Config).limit(1))
        config = result.scalar_one_or_none()
        if config is None or not config.enabled:
            return
        await m365.send_mail(
            tenant_id=config.tenant_id,
            client_id=config.client_id,
            client_secret=config.client_secret,
            sender_upn=config.sender_upn,
            to_email=to_email,
            subject=subject,
            body=body,
        )


async def send_teams_notification(ctx, to_upn: str, message: str) -> None:
    """Same delivery-only, best-effort posture as send_email_notification
    above - re-reads TeamsConfig itself rather than trusting the enabled
    check services.notifications.dispatch already did at enqueue time, in
    case an admin disabled it in the window between enqueue and this
    actually running."""
    async with SessionLocal() as db:
        result = await db.execute(select(TeamsConfig).limit(1))
        config = result.scalar_one_or_none()
        if config is None or not config.enabled:
            return
        await teams.send_activity_notification(
            tenant_id=config.tenant_id,
            client_id=config.client_id,
            client_secret=config.client_secret,
            teams_app_id=config.teams_app_id,
            to_upn=to_upn,
            message=message,
        )
