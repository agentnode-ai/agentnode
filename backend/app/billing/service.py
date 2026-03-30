import logging
import uuid
from datetime import datetime, timezone

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import ProcessedStripeEvent, ReviewRequest
from app.config import settings
from app.packages.models import Package, PackageVersion
from app.publishers.models import Publisher
from app.shared.exceptions import AppError

logger = logging.getLogger(__name__)

# --- Pricing ---

TIER_PRICES = {
    "security": 4900,       # $49
    "compatibility": 9900,  # $99
    "full": 19900,          # $199
}

EXPRESS_SURCHARGE = 10000  # $100


def calculate_price(tier: str, express: bool) -> int:
    """Calculate total price in cents for a review request."""
    base = TIER_PRICES.get(tier)
    if base is None:
        raise AppError("INVALID_TIER", f"Unknown review tier: {tier}", 400)
    return base + (EXPRESS_SURCHARGE if express else 0)


# --- Badge mapping ---

TIER_BADGE_COLUMN = {
    "security": "security_reviewed_at",
    "compatibility": "compatibility_reviewed_at",
    "full": "manually_reviewed_at",
}


# --- Review lifecycle ---

async def create_review_request(
    session: AsyncSession,
    *,
    publisher_id: uuid.UUID,
    package_slug: str,
    version: str,
    tier: str,
    express: bool,
) -> tuple[ReviewRequest, str]:
    """Create a new review request and return (review, checkout_url).

    Returns the review request and the Stripe Checkout URL.
    """
    from app.billing.stripe_client import create_review_checkout_session

    # Resolve package + version
    pkg_result = await session.execute(
        select(Package).where(Package.slug == package_slug)
    )
    pkg = pkg_result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{package_slug}' not found", 404)

    # Verify publisher owns this package
    if pkg.publisher_id != publisher_id:
        raise AppError("NOT_PACKAGE_OWNER", "You can only request reviews for your own packages", 403)

    pv_result = await session.execute(
        select(PackageVersion).where(
            PackageVersion.package_id == pkg.id,
            PackageVersion.version_number == version,
        )
    )
    pv = pv_result.scalar_one_or_none()
    if not pv:
        raise AppError("VERSION_NOT_FOUND", f"Version '{version}' not found", 404)

    # Check for existing pending/active review for this version+tier
    existing_result = await session.execute(
        select(ReviewRequest).where(
            ReviewRequest.package_version_id == pv.id,
            ReviewRequest.tier == tier,
            ReviewRequest.status.in_(["pending_payment", "paid", "in_review"]),
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise AppError(
            "REVIEW_ALREADY_EXISTS",
            f"An active review request already exists for this version and tier",
            409,
        )

    price_cents = calculate_price(tier, express)
    order_id = f"rev_{uuid.uuid4().hex}"

    review = ReviewRequest(
        order_id=order_id,
        publisher_id=publisher_id,
        package_id=pkg.id,
        package_version_id=pv.id,
        tier=tier,
        express=express,
        price_cents=price_cents,
        currency="usd",
        status="pending_payment",
    )
    session.add(review)
    await session.flush()

    # Create Stripe Checkout
    checkout_session = await _create_checkout(
        order_id=order_id,
        tier=tier,
        express=express,
        price_cents=price_cents,
    )

    return review, checkout_session.url


async def _create_checkout(*, order_id: str, tier: str, express: bool, price_cents: int):
    """Wrapper to call Stripe checkout creation."""
    from app.billing.stripe_client import create_review_checkout_session

    return await create_review_checkout_session(
        order_id=order_id,
        tier=tier,
        express=express,
        price_cents=price_cents,
        idempotency_key=f"checkout_{order_id}",
    )


# --- Webhook processing ---

async def process_stripe_event(session: AsyncSession, event: dict) -> dict:
    """Process a Stripe webhook event idempotently.

    Idempotency is enforced at two levels:
    1. SELECT check on processed_stripe_events (fast path)
    2. PK constraint on event_id catches parallel duplicates (IntegrityError)
    """
    event_id = event["id"]
    event_type = event["type"]

    # 1. Deduplicate — fast path
    existing = await session.execute(
        select(ProcessedStripeEvent).where(ProcessedStripeEvent.event_id == event_id)
    )
    if existing.scalar_one_or_none():
        return {"status": "already_processed"}

    # 2. Insert marker FIRST to claim this event (PK prevents parallel duplicates)
    processed = ProcessedStripeEvent(event_id=event_id, event_type=event_type)
    session.add(processed)
    try:
        await session.flush()
    except IntegrityError:
        # Parallel request already claimed this event
        await session.rollback()
        return {"status": "already_processed"}

    # 3. Process only explicit event types
    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(session, event["data"]["object"])
    elif event_type == "checkout.session.expired":
        await _handle_checkout_expired(session, event["data"]["object"])
    else:
        logger.info(f"Ignoring unhandled Stripe event type: {event_type}")

    return {"status": "processed"}


async def _handle_checkout_completed(session: AsyncSession, checkout_obj: dict):
    """Handle checkout.session.completed — mark review as paid."""
    order_id = checkout_obj.get("client_reference_id")
    if not order_id or not order_id.startswith("rev_"):
        return  # Not a review checkout

    result = await session.execute(
        select(ReviewRequest).where(ReviewRequest.order_id == order_id)
    )
    review = result.scalar_one_or_none()
    if not review:
        logger.warning(f"Review not found for order_id: {order_id}")
        return

    if review.status != "pending_payment":
        logger.info(f"Review {review.id} already processed (status={review.status})")
        return

    review.status = "paid"
    review.paid_at = datetime.now(timezone.utc)
    review.stripe_checkout_session_id = checkout_obj.get("id")
    review.stripe_payment_intent_id = checkout_obj.get("payment_intent")


async def _handle_checkout_expired(session: AsyncSession, checkout_obj: dict):
    """Handle checkout.session.expired — optional cleanup."""
    order_id = checkout_obj.get("client_reference_id")
    if not order_id or not order_id.startswith("rev_"):
        return

    result = await session.execute(
        select(ReviewRequest).where(ReviewRequest.order_id == order_id)
    )
    review = result.scalar_one_or_none()
    if review and review.status == "pending_payment":
        logger.info(f"Checkout expired for review {review.id}, cleaning up")
        await session.delete(review)


# --- Admin actions ---

async def assign_reviewer(
    session: AsyncSession,
    review_id: uuid.UUID,
    reviewer_id: uuid.UUID,
) -> ReviewRequest:
    """Assign a reviewer to a review request."""
    review = await _get_review_for_update(session, review_id)

    if review.status not in ("paid", "in_review"):
        raise AppError("INVALID_STATUS", f"Cannot assign reviewer to review in status '{review.status}'", 400)

    review.assigned_reviewer_id = reviewer_id
    review.status = "in_review"
    return review


async def complete_review(
    session: AsyncSession,
    review_id: uuid.UUID,
    outcome: str,
    notes: str | None,
    review_result: dict,
) -> ReviewRequest:
    """Complete a review with outcome and structured result. Materialize badge if approved."""
    review = await _get_review_for_update(session, review_id)

    if review.status != "in_review":
        raise AppError("INVALID_STATUS", f"Cannot complete review in status '{review.status}' (must be in_review)", 400)

    now = datetime.now(timezone.utc)
    review.status = outcome
    review.review_notes = notes
    review.review_result = review_result
    review.reviewed_at = now

    # Materialize badge on the package version if approved
    if outcome == "approved":
        badge_col = TIER_BADGE_COLUMN.get(review.tier)
        if badge_col:
            pv_result = await session.execute(
                select(PackageVersion).where(PackageVersion.id == review.package_version_id)
            )
            pv = pv_result.scalar_one_or_none()
            if pv:
                setattr(pv, badge_col, now)

    return review


async def process_refund(
    session: AsyncSession,
    review_id: uuid.UUID,
    amount_cents: int | None,
    reason: str,
) -> ReviewRequest:
    """Process a refund (full or partial) via Stripe."""
    review = await _get_review_for_update(session, review_id)

    if review.status == "refunded":
        raise AppError("ALREADY_REFUNDED", "This review has already been fully refunded", 400)

    if review.refund_amount_cents is not None:
        raise AppError("ALREADY_PARTIALLY_REFUNDED", "This review has already been partially refunded", 400)

    if not review.stripe_payment_intent_id:
        raise AppError("NO_PAYMENT", "No payment found for this review", 400)

    is_full_refund = amount_cents is None or amount_cents >= review.price_cents

    # Issue refund via Stripe
    if not settings.STRIPE_SECRET_KEY:
        raise AppError("BILLING_UNAVAILABLE", "Billing is not configured", 503)

    stripe.api_key = settings.STRIPE_SECRET_KEY
    refund_params: dict = {"payment_intent": review.stripe_payment_intent_id}
    if not is_full_refund:
        refund_params["amount"] = amount_cents

    stripe.Refund.create(**refund_params)

    if is_full_refund:
        review.status = "refunded"
        review.refund_amount_cents = review.price_cents

        # Remove materialized badge
        badge_col = TIER_BADGE_COLUMN.get(review.tier)
        if badge_col:
            pv_result = await session.execute(
                select(PackageVersion).where(PackageVersion.id == review.package_version_id)
            )
            pv = pv_result.scalar_one_or_none()
            if pv:
                setattr(pv, badge_col, None)
    else:
        # Partial refund — status stays, badge stays
        review.refund_amount_cents = amount_cents

    return review


async def _get_review(session: AsyncSession, review_id: uuid.UUID) -> ReviewRequest:
    """Get a review request by ID or raise 404 (read-only)."""
    result = await session.execute(
        select(ReviewRequest).where(ReviewRequest.id == review_id)
    )
    review = result.scalar_one_or_none()
    if not review:
        raise AppError("REVIEW_NOT_FOUND", "Review request not found", 404)
    return review


async def _get_review_for_update(session: AsyncSession, review_id: uuid.UUID) -> ReviewRequest:
    """Get a review request with row-level lock for mutation (prevents race conditions)."""
    result = await session.execute(
        select(ReviewRequest)
        .where(ReviewRequest.id == review_id)
        .with_for_update()
    )
    review = result.scalar_one_or_none()
    if not review:
        raise AppError("REVIEW_NOT_FOUND", "Review request not found", 404)
    return review
