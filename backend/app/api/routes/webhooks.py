import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import Role, User
from app.models.webhook import Webhook, WebhookDelivery
from app.schemas.webhook import (
    WebhookCreate,
    WebhookDeliveryRead,
    WebhookRead,
    WebhookTestResult,
    WebhookUpdate,
)
from app.security.rbac import get_current_user, require_role
from app.services import audit
from app.services.webhooks import deliver_once

router = APIRouter(prefix="/api/organizations/{org_id}/webhooks", tags=["webhooks"])

_admin = Depends(require_role(Role.ADMIN))


@router.get("", response_model=list[WebhookRead], dependencies=[_admin])
async def list_webhooks(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[Webhook]:
    result = await db.execute(select(Webhook).where(Webhook.org_id == org_id))
    return list(result.scalars().all())


@router.post("", response_model=WebhookRead, status_code=status.HTTP_201_CREATED, dependencies=[_admin])
async def create_webhook(
    org_id: uuid.UUID,
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Webhook:
    webhook = Webhook(org_id=org_id, name=body.name, url=body.url, enabled=body.enabled, events=body.events)
    webhook.secret = body.secret
    db.add(webhook)
    await db.flush()
    audit.record(
        db, action="webhook.create", target_type="webhook", org_id=org_id,
        user_id=current_user.id, target_id=webhook.id, detail={"name": webhook.name},
    )
    await db.commit()
    await db.refresh(webhook)
    return webhook


async def _get_org_webhook(db: AsyncSession, org_id: uuid.UUID, webhook_id: uuid.UUID) -> Webhook:
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id, Webhook.org_id == org_id))
    webhook = result.scalar_one_or_none()
    if webhook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook not found in this organization")
    return webhook


@router.patch("/{webhook_id}", response_model=WebhookRead, dependencies=[_admin])
async def update_webhook(
    org_id: uuid.UUID,
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Webhook:
    webhook = await _get_org_webhook(db, org_id, webhook_id)
    updates = body.model_dump(exclude_unset=True, exclude={"secret"})
    for field, value in updates.items():
        setattr(webhook, field, value)
    if body.secret:
        webhook.secret = body.secret
    audit.record(
        db, action="webhook.update", target_type="webhook", org_id=org_id,
        user_id=current_user.id, target_id=webhook.id, detail={"fields": list(updates.keys())},
    )
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_admin])
async def delete_webhook(
    org_id: uuid.UUID,
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    webhook = await _get_org_webhook(db, org_id, webhook_id)
    audit.record(
        db, action="webhook.delete", target_type="webhook", org_id=org_id,
        user_id=current_user.id, target_id=webhook.id, detail={"name": webhook.name},
    )
    await db.delete(webhook)
    await db.commit()


@router.post("/{webhook_id}/test", response_model=WebhookTestResult, dependencies=[_admin])
async def test_webhook(org_id: uuid.UUID, webhook_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> WebhookTestResult:
    webhook = await _get_org_webhook(db, org_id, webhook_id)
    status_code, success, response_text = await deliver_once(
        webhook, "test", {"message": "This is a test event from DeployCore."}
    )
    db.add(
        WebhookDelivery(
            webhook_id=webhook.id, event_type="test", status_code=status_code,
            success=success, response_snippet=response_text,
        )
    )
    await db.commit()
    return WebhookTestResult(ok=success, status_code=status_code, message=response_text or "no response body")


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryRead], dependencies=[_admin])
async def list_webhook_deliveries(
    org_id: uuid.UUID, webhook_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[WebhookDelivery]:
    await _get_org_webhook(db, org_id, webhook_id)
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.occurred_at.desc())
        .limit(20)
    )
    return list(result.scalars().all())
