"""Webhook delivery service — fires events to registered webhook URLs."""
import hashlib
import hmac
import json
import logging
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.webhooks.models import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)


async def fire_event(
    session: AsyncSession,
    publisher_id: UUID,
    event_type: str,
    payload: dict,
) -> None:
    """Find webhooks for this publisher+event and deliver."""
    result = await session.execute(
        select(Webhook).where(
            Webhook.publisher_id == publisher_id,
            Webhook.is_active == True,  # noqa: E712
        )
    )
    webhooks = result.scalars().all()

    for wh in webhooks:
        if event_type not in (wh.events or []):
            continue

        body = json.dumps({"event": event_type, "data": payload})
        headers = {"Content-Type": "application/json"}

        if wh.secret:
            sig = hmac.new(wh.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        status_code = None
        success = False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(wh.url, content=body, headers=headers, timeout=10)
                status_code = str(resp.status_code)
                success = 200 <= resp.status_code < 300
        except Exception as e:
            logger.error(f"Webhook delivery failed for {wh.url}: {e}")
            status_code = "error"

        delivery = WebhookDelivery(
            webhook_id=wh.id,
            event_type=event_type,
            payload=payload,
            status_code=status_code,
            success=success,
        )
        session.add(delivery)

    await session.commit()
