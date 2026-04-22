"""Webhook delivery service — fires events to registered webhook URLs."""
import hashlib
import hmac
import json
import logging
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.validators import resolve_public_ip
from app.webhooks.models import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

# Shared httpx client for outbound webhook deliveries.
# Initialized via init_webhook_client() at app startup, closed via close_webhook_client() at shutdown.
_webhook_client: httpx.AsyncClient | None = None


def init_webhook_client() -> None:
    """Create the shared webhook httpx client. Call once at app startup."""
    global _webhook_client
    _webhook_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        follow_redirects=False,
    )


async def close_webhook_client() -> None:
    """Close the shared webhook httpx client. Call once at app shutdown."""
    global _webhook_client
    if _webhook_client is not None:
        await _webhook_client.aclose()
        _webhook_client = None


def _get_webhook_client() -> httpx.AsyncClient:
    """Return the shared client, lazily initializing if needed."""
    global _webhook_client
    if _webhook_client is None:
        init_webhook_client()
    return _webhook_client


async def fire_event(
    publisher_id: UUID,
    event_type: str,
    payload: dict,
) -> None:
    """Find webhooks for this publisher+event and deliver.

    P1-L6: holding a DB session across outbound HTTP pinned a pooled
    connection for the duration of every webhook post (seconds to a full
    timeout on a slow endpoint). We now read the webhook targets in one
    session, exit the session, perform all HTTP deliveries, then reopen a
    short session to persist the WebhookDelivery rows.
    """
    from app.database import async_session_factory

    # Step 1: load targets, then close the session before any HTTP work.
    async with async_session_factory() as session:
        result = await session.execute(
            select(Webhook).where(
                Webhook.publisher_id == publisher_id,
                Webhook.is_active == True,  # noqa: E712
            )
        )
        targets: list[tuple[UUID, str, list[str] | None, str | None]] = [
            (wh.id, wh.url, wh.events, wh.secret)
            for wh in result.scalars().all()
        ]

    if not targets:
        return

    client = _get_webhook_client()
    deliveries: list[dict] = []

    # Step 2: deliver without holding a DB session.
    for wh_id, wh_url, wh_events, wh_secret in targets:
        if event_type not in (wh_events or []):
            continue

        body = json.dumps({"event": event_type, "data": payload})
        headers = {"Content-Type": "application/json"}
        if wh_secret:
            sig = hmac.new(wh_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        status_code: str | None = None
        success = False

        # P1-S2: delivery-time DNS check. Registration already ran
        # `is_safe_url(block_private=True)`, but between registration
        # and now an attacker could flip their DNS record to a private
        # IP (DNS rebinding / TOCTOU). Re-resolve here and refuse the
        # delivery if no public IP is returned. This is still racy
        # against the resolver used by httpx itself, but the window is
        # sub-second rather than unbounded.
        parsed = urlparse(wh_url)
        hostname = parsed.hostname or ""
        if not hostname or resolve_public_ip(hostname) is None:
            logger.error("Webhook delivery refused for %s: host resolves to non-public IP", wh_url)
            status_code = "blocked"
        else:
            try:
                resp = await client.post(wh_url, content=body, headers=headers)
                status_code = str(resp.status_code)
                success = 200 <= resp.status_code < 300
            except Exception as e:
                logger.error(f"Webhook delivery failed for {wh_url}: {e}")
                status_code = "error"

        deliveries.append({
            "webhook_id": wh_id,
            "event_type": event_type,
            "payload": payload,
            "status_code": status_code,
            "success": success,
        })

    if not deliveries:
        return

    # Step 3: reopen a short session to persist history.
    try:
        async with async_session_factory() as session:
            for d in deliveries:
                session.add(WebhookDelivery(**d))
            await session.commit()
    except Exception:
        logger.exception("Failed to persist webhook delivery records")
