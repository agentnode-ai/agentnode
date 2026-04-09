import logging
from uuid import UUID

import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin.models import AdminAuditLog
from app.auth.dependencies import get_current_user, require_admin, require_publisher
from app.auth.models import User
from app.billing.models import ReviewRequest
from app.packages.service import build_meili_document
from app.shared.meili import sync_package_to_meilisearch, delete_package_from_meilisearch
from app.billing.schemas import (
    AdminQueueItem,
    AssignReviewerBody,
    CompleteReviewBody,
    CreateReviewRequestBody,
    RefundReviewBody,
    ReviewCheckoutResponse,
    ReviewRequestResponse,
)
from app.billing.service import (
    assign_reviewer,
    complete_review,
    create_review_request,
    process_refund,
    process_stripe_event,
    _get_review_email_context,
)
from app.billing.stripe_client import verify_webhook_signature
from app.database import get_session
from app.packages.models import Package, PackageVersion
from app.publishers.models import Publisher
from app.shared.email import (
    send_review_assigned_email,
    send_review_payment_received_email,
    send_review_completed_email,
    send_review_refund_email,
)
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])


# ---- Publisher endpoints ----


@router.post(
    "/v1/reviews/request",
    response_model=ReviewCheckoutResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
async def request_review(
    body: CreateReviewRequestBody,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Request a paid code review for a specific package version."""
    review, checkout_url = await create_review_request(
        session,
        publisher_id=user.publisher.id,
        package_slug=body.package_slug,
        version=body.version,
        tier=body.tier,
        express=body.express,
    )
    await session.commit()

    return ReviewCheckoutResponse(
        review_id=review.id,
        order_id=review.order_id,
        checkout_url=checkout_url,
        price_cents=review.price_cents,
        tier=review.tier,
        express=review.express,
    )


@router.get("/v1/reviews/my", response_model=list[ReviewRequestResponse], dependencies=[Depends(rate_limit(30, 60))])
async def my_reviews(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List the current user's review requests."""
    if not user.publisher:
        return []

    result = await session.execute(
        select(ReviewRequest)
        .where(ReviewRequest.publisher_id == user.publisher.id)
        .order_by(ReviewRequest.created_at.desc())
        .limit(50)
    )
    reviews = result.scalars().all()

    # Batch-load package + version info (2 queries total instead of 2*N)
    ctx = await _batch_review_context(session, reviews)
    items = []
    for r in reviews:
        pkg_name, pkg_slug, ver, _, _, _ = ctx.get(r.id, (None, None, None, None, None, None))
        items.append(
            ReviewRequestResponse(
                id=r.id,
                order_id=r.order_id,
                package_slug=pkg_slug,
                package_name=pkg_name,
                version=ver,
                tier=r.tier,
                express=r.express,
                price_cents=r.price_cents,
                currency=r.currency,
                status=r.status,
                review_notes=r.review_notes,
                review_result=r.review_result,
                refund_amount_cents=r.refund_amount_cents,
                paid_at=r.paid_at,
                reviewed_at=r.reviewed_at,
                created_at=r.created_at,
            )
        )
    return items


@router.get("/v1/reviews/{review_id}", response_model=ReviewRequestResponse, dependencies=[Depends(rate_limit(30, 60))])
async def get_review(
    review_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get a single review request (owner only)."""
    result = await session.execute(
        select(ReviewRequest).where(ReviewRequest.id == review_id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise AppError("REVIEW_NOT_FOUND", "Review request not found", 404)

    # Check ownership (publisher or admin)
    if not user.is_admin and (not user.publisher or r.publisher_id != user.publisher.id):
        raise AppError("REVIEW_NOT_FOUND", "Review request not found", 404)

    pkg_name, pkg_slug, ver = await _get_review_context(session, r)
    return ReviewRequestResponse(
        id=r.id,
        order_id=r.order_id,
        package_slug=pkg_slug,
        package_name=pkg_name,
        version=ver,
        tier=r.tier,
        express=r.express,
        price_cents=r.price_cents,
        currency=r.currency,
        status=r.status,
        review_notes=r.review_notes,
        review_result=r.review_result,
        refund_amount_cents=r.refund_amount_cents,
        paid_at=r.paid_at,
        reviewed_at=r.reviewed_at,
        created_at=r.created_at,
    )


# ---- Stripe webhook ----


@router.post("/v1/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Handle Stripe webhook events (signature-verified, idempotent)."""
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = verify_webhook_signature(body, sig)
    except AppError:
        raise  # Let 503 "Billing unavailable" propagate as-is
    except stripe.SignatureVerificationError as e:
        logger.warning(f"Stripe webhook signature verification failed: {e}")
        raise AppError("WEBHOOK_INVALID", "Invalid webhook signature", 400)
    except ValueError as e:
        logger.warning(f"Stripe webhook payload invalid: {e}")
        raise AppError("WEBHOOK_INVALID", "Invalid webhook payload", 400)

    result = await process_stripe_event(session, event)
    await session.commit()

    # Send payment received email after commit (background — don't block webhook response)
    if result.get("status") == "processed" and event["type"] == "checkout.session.completed":
        try:
            order_id = event["data"]["object"].get("client_reference_id", "")
            if order_id.startswith("rev_"):
                rr = await session.execute(
                    select(ReviewRequest).where(ReviewRequest.order_id == order_id)
                )
                review = rr.scalar_one_or_none()
                if review and review.status == "paid":
                    slug, ver, email = await _get_review_email_context(session, review.id)
                    if email:
                        background_tasks.add_task(
                            send_review_payment_received_email,
                            email, slug, ver, review.tier, review.express, review.price_cents,
                        )
        except Exception:
            logger.warning("Failed to prepare review payment email", exc_info=True)

    return result


# ---- Admin endpoints ----


@router.get("/v1/admin/reviews/queue", response_model=list[AdminQueueItem], dependencies=[Depends(rate_limit(30, 60))])
async def admin_review_queue(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List paid review requests, express first, then FIFO."""
    result = await session.execute(
        select(ReviewRequest)
        .where(ReviewRequest.status.in_(["paid", "in_review"]))
        .order_by(
            ReviewRequest.express.desc(),  # Express first
            ReviewRequest.paid_at.asc(),   # Then FIFO
        )
        .limit(100)
    )
    reviews = result.scalars().all()

    # Batch-load all context (3 queries total instead of 3*N)
    ctx = await _batch_review_context(session, reviews)
    publisher_ids = {r.publisher_id for r in reviews if r.publisher_id is not None}
    pub_ctx = await _batch_publisher_context(session, publisher_ids)

    items = []
    for r in reviews:
        pkg_name, pkg_slug, ver, v_status, v_tier, v_score = ctx.get(r.id, (None, None, None, None, None, None))
        pub_slug, pub_name = pub_ctx.get(r.publisher_id, (None, None))
        items.append(
            AdminQueueItem(
                id=r.id,
                order_id=r.order_id,
                package_slug=pkg_slug,
                package_name=pkg_name,
                version=ver,
                tier=r.tier,
                express=r.express,
                price_cents=r.price_cents,
                currency=r.currency,
                status=r.status,
                review_notes=r.review_notes,
                review_result=r.review_result,
                refund_amount_cents=r.refund_amount_cents,
                paid_at=r.paid_at,
                reviewed_at=r.reviewed_at,
                created_at=r.created_at,
                publisher_slug=pub_slug,
                publisher_name=pub_name,
                assigned_reviewer_id=r.assigned_reviewer_id,
                verification_status=v_status,
                verification_tier=v_tier,
                verification_score=v_score,
            )
        )
    return items


@router.post("/v1/admin/reviews/{review_id}/assign", dependencies=[Depends(rate_limit(10, 60))])
async def admin_assign_reviewer(
    review_id: UUID,
    body: AssignReviewerBody,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Assign a reviewer to a review request."""
    review = await assign_reviewer(session, review_id, body.reviewer_id)
    await _audit(session, request, user, "assign_reviewer", "review", str(review_id), {
        "reviewer_id": str(body.reviewer_id),
    })
    await session.commit()

    # Send "reviewer assigned" email in background
    try:
        slug, ver, email = await _get_review_email_context(session, review.id)
        if email:
            background_tasks.add_task(
                send_review_assigned_email,
                email, slug, ver, review.tier, review.express,
            )
    except Exception:
        logger.warning("Failed to prepare review assigned email", exc_info=True)

    return {"status": "assigned", "review_id": str(review.id)}


@router.post("/v1/admin/reviews/{review_id}/complete", dependencies=[Depends(rate_limit(10, 60))])
async def admin_complete_review(
    review_id: UUID,
    body: CompleteReviewBody,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Complete a review with outcome and structured result."""
    review = await complete_review(
        session,
        review_id,
        outcome=body.outcome,
        notes=body.notes,
        review_result=body.review_result,
    )
    await _audit(session, request, user, "complete_review", "review", str(review_id), {
        "outcome": body.outcome,
        "tier": review.tier,
    })
    await session.commit()

    # Send completion email in background (don't block response)
    try:
        slug, ver, email = await _get_review_email_context(session, review.id)
        if email:
            background_tasks.add_task(
                send_review_completed_email,
                email, slug, ver, review.tier,
                body.outcome, body.review_result, body.notes,
            )
    except Exception:
        logger.warning("Failed to prepare review completion email", exc_info=True)

    # Sync badge to Meilisearch after commit (fire-and-forget)
    if body.outcome == "approved":
        try:
            await _sync_review_badge_to_search(session, review.package_id)
        except Exception:
            logger.warning("Failed to sync badge to Meilisearch", exc_info=True)

    return {
        "status": body.outcome,
        "review_id": str(review.id),
        "badge_materialized": body.outcome == "approved",
    }


@router.post("/v1/admin/reviews/{review_id}/refund", dependencies=[Depends(rate_limit(5, 60))])
async def admin_refund_review(
    review_id: UUID,
    body: RefundReviewBody,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Issue a full or partial refund for a review."""
    review = await process_refund(
        session,
        review_id,
        amount_cents=body.amount_cents,
        reason=body.reason,
    )
    is_full = body.amount_cents is None or body.amount_cents >= review.price_cents
    await _audit(session, request, user, "refund_review", "review", str(review_id), {
        "amount_cents": review.refund_amount_cents,
        "full_refund": is_full,
        "reason": body.reason,
    })
    await session.commit()

    # Send refund email in background (don't block response)
    try:
        slug, ver, email = await _get_review_email_context(session, review.id)
        if email:
            background_tasks.add_task(
                send_review_refund_email,
                email, slug, ver, review.refund_amount_cents, is_full,
            )
    except Exception:
        logger.warning("Failed to prepare review refund email", exc_info=True)

    # Sync badge removal to Meilisearch after full refund (fire-and-forget)
    if is_full:
        try:
            await _sync_review_badge_to_search(session, review.package_id)
        except Exception:
            logger.warning("Failed to sync badge removal to Meilisearch", exc_info=True)

    return {
        "status": review.status,
        "refund_amount_cents": review.refund_amount_cents,
        "badge_removed": is_full,
    }


# ---- Meilisearch sync ----


async def _sync_review_badge_to_search(session: AsyncSession, package_id) -> None:
    """Sync review badge changes to Meilisearch. Fire-and-forget, never raises."""
    from sqlalchemy.orm import selectinload

    pkg_result = await session.execute(
        select(Package)
        .options(selectinload(Package.publisher))
        .where(Package.id == package_id)
    )
    pkg = pkg_result.scalar_one_or_none()
    if not pkg:
        return

    # Find current public version (latest)
    pv_result = await session.execute(
        select(PackageVersion)
        .where(
            PackageVersion.package_id == package_id,
            PackageVersion.is_yanked == False,  # noqa: E712
        )
        .order_by(PackageVersion.published_at.desc())
        .limit(1)
    )
    pv = pv_result.scalar_one_or_none()
    if not pv:
        await delete_package_from_meilisearch(pkg.slug)
        return

    # Check if package is indexable
    if pkg.is_deprecated and pkg.download_count == 0:
        await delete_package_from_meilisearch(pkg.slug)
        return

    manifest = pv.manifest or {}
    doc = build_meili_document(pkg, pv, manifest)
    await sync_package_to_meilisearch(doc)


# ---- Helpers ----


async def _get_review_context(session: AsyncSession, review: ReviewRequest) -> tuple[str | None, str | None, str | None]:
    """Return (package_name, package_slug, version_number) for a review."""
    pkg_result = await session.execute(
        select(Package.name, Package.slug).where(Package.id == review.package_id)
    )
    pkg_row = pkg_result.one_or_none()
    pv_result = await session.execute(
        select(PackageVersion.version_number).where(PackageVersion.id == review.package_version_id)
    )
    pv_row = pv_result.one_or_none()
    return (
        pkg_row.name if pkg_row else None,
        pkg_row.slug if pkg_row else None,
        pv_row.version_number if pv_row else None,
    )


async def _batch_review_context(
    session: AsyncSession, reviews: list[ReviewRequest],
) -> dict[UUID, tuple[str | None, str | None, str | None, str | None, str | None, int | None]]:
    """Batch-load (package_name, package_slug, version_number, verification_status, verification_tier, verification_score) for many reviews.

    Returns a dict keyed by review.id.  Two queries total instead of 2*N.
    """
    if not reviews:
        return {}

    package_ids = {r.package_id for r in reviews if r.package_id is not None}
    version_ids = {r.package_version_id for r in reviews if r.package_version_id is not None}

    # Single query for all packages
    pkg_map: dict[UUID, tuple[str | None, str | None]] = {}
    if package_ids:
        pkg_result = await session.execute(
            select(Package.id, Package.name, Package.slug).where(Package.id.in_(package_ids))
        )
        for row in pkg_result.all():
            pkg_map[row.id] = (row.name, row.slug)

    # Single query for all versions (include verification fields)
    ver_map: dict[UUID, tuple[str, str | None, str | None, int | None]] = {}
    if version_ids:
        pv_result = await session.execute(
            select(
                PackageVersion.id,
                PackageVersion.version_number,
                PackageVersion.verification_status,
                PackageVersion.verification_tier,
                PackageVersion.verification_score,
            ).where(PackageVersion.id.in_(version_ids))
        )
        for row in pv_result.all():
            ver_map[row.id] = (row.version_number, row.verification_status, row.verification_tier, row.verification_score)

    # Assemble per-review context
    result: dict[UUID, tuple[str | None, str | None, str | None, str | None, str | None, int | None]] = {}
    for r in reviews:
        pkg_name, pkg_slug = pkg_map.get(r.package_id, (None, None))
        ver_info = ver_map.get(r.package_version_id)
        if ver_info:
            ver, v_status, v_tier, v_score = ver_info
        else:
            ver, v_status, v_tier, v_score = None, None, None, None
        result[r.id] = (pkg_name, pkg_slug, ver, v_status, v_tier, v_score)
    return result


async def _batch_publisher_context(
    session: AsyncSession, publisher_ids: set[UUID],
) -> dict[UUID, tuple[str | None, str | None]]:
    """Batch-load (publisher_slug, publisher_name) for many publisher IDs.

    Returns a dict keyed by publisher_id.  One query instead of N.
    """
    if not publisher_ids:
        return {}

    result = await session.execute(
        select(Publisher.id, Publisher.slug, Publisher.display_name).where(Publisher.id.in_(publisher_ids))
    )
    pub_map: dict[UUID, tuple[str | None, str | None]] = {}
    for row in result.all():
        pub_map[row.id] = (row.slug, row.display_name)
    return pub_map


async def _get_publisher_context(session: AsyncSession, publisher_id) -> tuple[str | None, str | None]:
    """Return (publisher_slug, publisher_name)."""
    result = await session.execute(
        select(Publisher.slug, Publisher.display_name).where(Publisher.id == publisher_id)
    )
    row = result.one_or_none()
    return (row.slug if row else None, row.display_name if row else None)


async def _audit(
    session: AsyncSession, request: Request, admin: User,
    action: str, target_type: str, target_id: str, metadata: dict | None = None,
) -> None:
    """Log an admin action (same pattern as admin/router.py)."""
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    log = AdminAuditLog(
        admin_user_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata_=metadata or {},
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    session.add(log)
    await session.flush()
