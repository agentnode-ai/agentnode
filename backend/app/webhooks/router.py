from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_publisher
from app.auth.models import User
from app.database import get_session
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit
from app.shared.validators import is_safe_url
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
    if not is_safe_url(body.url, block_private=True):
        raise AppError("INVALID_URL", "Webhook URL must be a public https:// or http:// URL", 422)
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


@router.get("", dependencies=[Depends(rate_limit(30, 60))])
async def list_webhooks(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """List all webhooks for the current publisher."""
    total_result = await session.execute(
        select(func.count(Webhook.id)).where(Webhook.publisher_id == user.publisher.id)
    )
    total = total_result.scalar() or 0

    result = await session.execute(
        select(Webhook)
        .where(Webhook.publisher_id == user.publisher.id)
        .order_by(Webhook.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    webhooks = result.scalars().all()

    return {
        "items": [
            WebhookResponse(
                id=wh.id,
                url=wh.url,
                events=wh.events,
                is_active=wh.is_active,
                created_at=wh.created_at,
            ).model_dump()
            for wh in webhooks
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.delete("/{webhook_id}", dependencies=[Depends(rate_limit(10, 60))])
async def delete_webhook(
    webhook_id: UUID,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Delete a webhook."""
    result = await session.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.publisher_id == user.publisher.id,
        )
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise AppError("WEBHOOK_NOT_FOUND", "Webhook not found", 404)

    await session.delete(wh)
    await session.commit()
    return {"deleted": True}


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryItem], dependencies=[Depends(rate_limit(30, 60))])
async def list_deliveries(
    webhook_id: UUID,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """List recent deliveries for a webhook."""
    # Verify ownership
    wh_result = await session.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.publisher_id == user.publisher.id,
        )
    )
    if not wh_result.scalar_one_or_none():
        raise AppError("WEBHOOK_NOT_FOUND", "Webhook not found", 404)

    result = await session.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
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
