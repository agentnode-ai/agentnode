import logging

import stripe
from fastapi import Request

from app.config import settings

logger = logging.getLogger(__name__)


def _ensure_stripe():
    """Raise 503 if Stripe keys are not configured."""
    if not settings.STRIPE_SECRET_KEY:
        from app.shared.exceptions import AppError
        raise AppError("BILLING_UNAVAILABLE", "Billing is not configured", 503)
    stripe.api_key = settings.STRIPE_SECRET_KEY


async def create_review_checkout_session(
    *,
    order_id: str,
    tier: str,
    express: bool,
    price_cents: int,
    idempotency_key: str,
) -> stripe.checkout.Session:
    """Create a Stripe Checkout Session for a review request."""
    _ensure_stripe()

    description = f"AgentNode {tier.title()} Review"
    if express:
        description += " (Express)"

    session_params: dict = {
        "mode": "payment",
        "client_reference_id": order_id,
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": price_cents,
                    "product_data": {
                        "name": description,
                        "description": f"Manual code review service — {tier} tier",
                    },
                },
                "quantity": 1,
            }
        ],
        "success_url": f"{settings.AGENTNODE_BASE_URL}/dashboard?review=success",
        "cancel_url": f"{settings.AGENTNODE_BASE_URL}/dashboard?review=cancelled",
    }

    if settings.STRIPE_TAX_ENABLED:
        session_params["automatic_tax"] = {"enabled": True}

    checkout_session = stripe.checkout.Session.create(
        **session_params,
        idempotency_key=idempotency_key,
    )

    return checkout_session


def verify_webhook_signature(request_body: bytes, sig_header: str) -> dict:
    """Verify and parse a Stripe webhook event."""
    _ensure_stripe()

    if not settings.STRIPE_WEBHOOK_SECRET:
        from app.shared.exceptions import AppError
        raise AppError("BILLING_UNAVAILABLE", "Webhook secret not configured", 503)

    event = stripe.Webhook.construct_event(
        request_body,
        sig_header,
        settings.STRIPE_WEBHOOK_SECRET,
    )
    return event
