from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_publisher
from app.auth.models import User
from app.database import get_session
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit
from app.webhooks.models import Webhook, WebhookDelivery
from app.webhooks.schemas import (
    CreateWebhookRequest,
    VALID_EVENTS,
    WebhookDeliveryItem,
    WebhookResponse,
)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookResponse, status_code=201, dependencies=[Depends(rate_limit(10, 60))])
async def create_webhook(
    body: CreateWebhookRequest,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Register a webhook for package events."""
    invalid = [e for e in body.events if e not in VALID_EVENTS]
    if invalid:
        raise AppError("INVALID_EVENTS", f"Invalid event types: {invalid}", 422)

    wh = Webhook(
        publisher_id=user.publisher.id,
        url=body.url,
        secret=body.secret,
        events=body.events,
    )
    session.add(wh)
    await session.commit()
    await session.refresh(wh)

    return WebhookResponse(
        id=wh.id,
        url=wh.url,
        events=wh.events,
        is_active=wh.is_active,
        created_at=wh.created_at,
    )


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """List all webhooks for the current publisher."""
    result = await session.execute(
        select(Webhook).where(Webhook.publisher_id == user.publisher.id)
    )
    webhooks = result.scalars().all()

    return [
        WebhookResponse(
            id=wh.id,
            url=wh.url,
            events=wh.events,
            is_active=wh.is_active,
            created_at=wh.created_at,
        )
        for wh in webhooks
    ]


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Delete a webhook."""
    from uuid import UUID
    result = await session.execute(
        select(Webhook).where(
            Webhook.id == UUID(webhook_id),
            Webhook.publisher_id == user.publisher.id,
        )
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise AppError("WEBHOOK_NOT_FOUND", "Webhook not found", 404)

    await session.delete(wh)
    await session.commit()
    return {"deleted": True}


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryItem])
async def list_deliveries(
    webhook_id: str,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """List recent deliveries for a webhook."""
    from uuid import UUID
    # Verify ownership
    wh_result = await session.execute(
        select(Webhook).where(
            Webhook.id == UUID(webhook_id),
            Webhook.publisher_id == user.publisher.id,
        )
    )
    if not wh_result.scalar_one_or_none():
        raise AppError("WEBHOOK_NOT_FOUND", "Webhook not found", 404)

    result = await session.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == UUID(webhook_id))
        .order_by(WebhookDelivery.delivered_at.desc())
        .limit(50)
    )
    deliveries = result.scalars().all()

    return [
        WebhookDeliveryItem(
            id=d.id,
            event_type=d.event_type,
            status_code=d.status_code,
            success=d.success,
            delivered_at=d.delivered_at,
        )
        for d in deliveries
    ]
