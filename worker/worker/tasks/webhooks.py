import asyncio
import uuid

from app.db import SessionLocal
from app.models.webhook import Webhook, WebhookDelivery
from app.services.webhooks import deliver_once

MAX_ATTEMPTS = 3


async def deliver_webhook(ctx, webhook_id: str, event_type: str, data: dict) -> None:
    async with SessionLocal() as db:
        webhook = await db.get(Webhook, uuid.UUID(webhook_id))
        if webhook is None or not webhook.enabled:
            return

        status_code, success, response_text = None, False, ""
        for attempt in range(MAX_ATTEMPTS):
            status_code, success, response_text = await deliver_once(webhook, event_type, data)
            if success:
                break
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)

        db.add(
            WebhookDelivery(
                webhook_id=webhook.id,
                event_type=event_type,
                status_code=status_code,
                success=success,
                response_snippet=response_text,
            )
        )
        await db.commit()
