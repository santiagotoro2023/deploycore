import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import Webhook

WEBHOOK_TIMEOUT_SECONDS = 10


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def build_payload(event_type: str, data: dict) -> str:
    return json.dumps({"event": event_type, "occurred_at": datetime.now(timezone.utc).isoformat(), "data": data})


async def deliver_once(webhook: Webhook, event_type: str, data: dict) -> tuple[int | None, bool, str]:
    """One HTTP attempt, no retry: used directly by the ad-hoc test-webhook
    route (immediate ok/fail for the UI) and by the worker's deliver_webhook
    task (which wraps this with retries)."""
    body = build_payload(event_type, data)
    headers = {
        "Content-Type": "application/json",
        "X-DeployCore-Signature": f"sha256={_sign(webhook.secret, body)}",
    }
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            response = await client.post(webhook.url, content=body, headers=headers)
        return response.status_code, response.status_code < 400, response.text[:500]
    except Exception as exc:  # noqa: BLE001 - network/DNS/TLS failure, caller decides retry/log
        return None, False, str(exc)[:500]


async def dispatch(db: AsyncSession, pool, org_id: uuid.UUID, event_type: str, data: dict) -> None:
    """Enqueues one deliver_webhook job per enabled webhook in this org that
    is subscribed to event_type. `pool` is anything with an async
    enqueue_job method: app.jobs.get_arq_pool() in the API process, or
    ctx["redis"] inside a worker task, same convention as
    services.notifications.maybe_email."""
    result = await db.execute(select(Webhook).where(Webhook.org_id == org_id, Webhook.enabled.is_(True)))
    for webhook in result.scalars().all():
        if event_type in webhook.events:
            await pool.enqueue_job("deliver_webhook", str(webhook.id), event_type, data)
