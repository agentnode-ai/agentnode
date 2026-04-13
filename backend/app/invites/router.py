from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.auth.models import User
from app.config import settings
from app.database import get_session
from app.invites.models import CandidateEvent, ImportCandidate, InviteCode
from app.invites.schemas import (
    BulkSendRequest,
    BulkSendResponse,
    CandidateCreateRequest,
    CandidateListResponse,
    CandidateResponse,
    CandidateUpdateRequest,
    EmailSentRequest,
    EventListResponse,
    EventResponse,
    FunnelResponse,
    InviteAdminResponse,
    InviteClaimResponse,
    InviteGenerateRequest,
    InviteGenerateResponse,
    InviteListResponse,
    InvitePublicResponse,
)
from app.invites.service import (
    claim_invite,
    create_invite_for_candidate,
    get_funnel_stats,
    get_invite_by_code,
    log_event,
)
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit

# ── Public router ──

router = APIRouter(prefix="/v1", tags=["invites"])


@router.get("/invites/{code}", response_model=InvitePublicResponse, dependencies=[Depends(rate_limit(10, 60))])
async def get_invite(code: str, session: AsyncSession = Depends(get_session)):
    """Public: get invite info (no sensitive data)."""
    invite = await get_invite_by_code(session, code)
    if not invite or invite.status in ("expired", "revoked", "claimed"):
        raise AppError("INVITE_NOT_FOUND", "Invite not found or no longer valid", 404)

    # Check TTL
    if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
        invite.status = "expired"
        await session.commit()
        raise AppError("INVITE_EXPIRED", "This invite has expired", 404)

    # Load candidate for display fields
    display_name = None
    description = None
    source_url = None
    if invite.candidate_id:
        result = await session.execute(
            select(ImportCandidate).where(ImportCandidate.id == invite.candidate_id)
        )
        candidate = result.scalar_one_or_none()
        if candidate:
            display_name = candidate.display_name
            description = candidate.description
            source_url = candidate.source_url

    # Log invite_viewed event (deduplicated: max once per code per 60 min)
    if invite.candidate_id:
        recent = await session.execute(
            select(CandidateEvent.id).where(
                CandidateEvent.candidate_id == invite.candidate_id,
                CandidateEvent.event_type == "invite_viewed",
                CandidateEvent.created_at >= datetime.now(timezone.utc) - timedelta(minutes=60),
            ).limit(1)
        )
        if not recent.scalar_one_or_none():
            await log_event(session, invite.candidate_id, "invite_viewed", {"invite_code": code})
            await session.commit()

    return InvitePublicResponse(
        code=code,
        valid=True,
        display_name=display_name,
        description=description,
        source_url=source_url,
    )


@router.post("/invites/{code}/claim", response_model=InviteClaimResponse, dependencies=[Depends(rate_limit(10, 60))])
async def claim_invite_endpoint(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Claim an invite code. Returns prefill_data."""
    prefill_data = await claim_invite(session, code, user.id)
    await session.commit()
    return InviteClaimResponse(prefill_data=prefill_data)


@router.get("/i/{code}", dependencies=[Depends(rate_limit(10, 60))])
async def tracking_redirect(code: str, session: AsyncSession = Depends(get_session)):
    """Track link click and redirect to invite landing page."""
    invite = await get_invite_by_code(session, code)
    if invite and invite.candidate_id:
        await log_event(session, invite.candidate_id, "invite_link_clicked", {"invite_code": code})
        await session.commit()

    base = settings.FRONTEND_URL if hasattr(settings, "FRONTEND_URL") else "https://agentnode.net"
    return RedirectResponse(url=f"{base}/invite/{code}", status_code=302)


@router.post("/invites/{code}/published", dependencies=[Depends(rate_limit(10, 60))])
async def mark_published_callback(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Fallback: client-side callback (server hook is primary)."""
    invite = await get_invite_by_code(session, code)
    if not invite or not invite.candidate_id:
        return {"ok": True}  # Silently ignore if no candidate

    if invite.claimed_by_user_id != user.id:
        return {"ok": True}  # Ignore if different user

    # P1-L8: guard against:
    #   a) users without a publisher row (the previous `hasattr`-trick produced
    #      a broken `WHERE false` predicate that SQLAlchemy warned on);
    #   b) race where the candidate's latest package is deleted/yanked between
    #      our SELECT and the update in `mark_candidate_published`. We filter
    #      yanked packages out of the lookup and fail gracefully if the row
    #      disappears mid-callback rather than crashing with a FK/500.
    publisher_id = getattr(user, "publisher_id", None)
    if publisher_id is None:
        return {"ok": True}

    from app.packages.models import Package
    result = await session.execute(
        select(Package.id)
        .where(
            Package.publisher_id == publisher_id,
            Package.is_deprecated == False,  # noqa: E712
        )
        .order_by(Package.created_at.desc())
        .limit(1)
    )
    pkg_row = result.first()

    if pkg_row:
        try:
            from app.invites.service import mark_candidate_published
            await mark_candidate_published(session, invite.candidate_id, pkg_row[0])
            await session.commit()
        except Exception:
            # Candidate package disappeared between SELECT and update, or any
            # other transient failure — callback is best-effort; the server-
            # side webhook is primary.
            await session.rollback()
            logger.warning("mark_published_callback: update failed for invite %s", code, exc_info=True)

    return {"ok": True}


# ── Admin router ──

admin_router = APIRouter(prefix="/v1/admin", tags=["admin-invites"])


@admin_router.get("/candidates/funnel", response_model=FunnelResponse)
async def funnel_stats(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Aggregated funnel data."""
    stats = await get_funnel_stats(session)
    return FunnelResponse(**stats)


@admin_router.get("/candidates", response_model=CandidateListResponse)
async def list_candidates(
    outreach_status: str | None = None,
    source: str | None = None,
    detected_format: str | None = None,
    min_stars: int | None = None,
    assigned_admin_id: UUID | None = None,
    funnel_filter: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List candidates with filtering."""
    query = select(ImportCandidate)
    count_query = select(func.count(ImportCandidate.id))

    conditions = []
    if outreach_status:
        conditions.append(ImportCandidate.outreach_status == outreach_status)
    if source:
        conditions.append(ImportCandidate.source == source)
    if detected_format:
        conditions.append(ImportCandidate.detected_format == detected_format)
    if min_stars:
        conditions.append(ImportCandidate.stars >= min_stars)
    if assigned_admin_id:
        conditions.append(ImportCandidate.assigned_admin_id == assigned_admin_id)

    # Funnel filters: subquery-based
    if funnel_filter == "clicked_not_signed_up":
        clicked_ids = select(CandidateEvent.candidate_id).where(
            CandidateEvent.event_type == "invite_link_clicked"
        ).distinct()
        claimed_ids = select(CandidateEvent.candidate_id).where(
            CandidateEvent.event_type == "invite_claimed"
        ).distinct()
        conditions.append(ImportCandidate.id.in_(clicked_ids))
        conditions.append(ImportCandidate.id.notin_(claimed_ids))
    elif funnel_filter == "signed_up_not_published":
        conditions.append(ImportCandidate.outreach_status == "signed_up")
    elif funnel_filter == "published_not_verified":
        conditions.append(ImportCandidate.outreach_status == "published")
        verified_ids = select(CandidateEvent.candidate_id).where(
            CandidateEvent.event_type == "verification_passed"
        ).distinct()
        conditions.append(ImportCandidate.id.notin_(verified_ids))

    if conditions:
        query = query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    # Total count
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # Paginated results
    query = query.order_by(ImportCandidate.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await session.execute(query)
    candidates = result.scalars().all()

    # Batch-load invite info and click counts (avoid N+1 queries)
    candidate_ids = [c.id for c in candidates]

    invite_map: dict = {}  # candidate_id -> (code, status)
    click_map: dict = {}   # candidate_id -> int

    if candidate_ids:
        # Latest invite per candidate via window function
        row_num = func.row_number().over(
            partition_by=InviteCode.candidate_id,
            order_by=InviteCode.created_at.desc(),
        ).label("rn")
        invite_sub = (
            select(
                InviteCode.candidate_id,
                InviteCode.code,
                InviteCode.status,
                row_num,
            )
            .where(InviteCode.candidate_id.in_(candidate_ids))
            .subquery()
        )
        invite_result = await session.execute(
            select(invite_sub.c.candidate_id, invite_sub.c.code, invite_sub.c.status)
            .where(invite_sub.c.rn == 1)
        )
        for row in invite_result.all():
            invite_map[row[0]] = (row[1], row[2])

        # Click counts per candidate in one query
        click_result = await session.execute(
            select(CandidateEvent.candidate_id, func.count(CandidateEvent.id))
            .where(and_(
                CandidateEvent.candidate_id.in_(candidate_ids),
                CandidateEvent.event_type == "invite_link_clicked",
            ))
            .group_by(CandidateEvent.candidate_id)
        )
        for row in click_result.all():
            click_map[row[0]] = row[1]

    items = []
    for c in candidates:
        invite_info = invite_map.get(c.id)
        items.append(CandidateResponse(
            id=c.id,
            source=c.source,
            source_url=c.source_url,
            repo_owner=c.repo_owner,
            repo_name=c.repo_name,
            display_name=c.display_name,
            description=c.description,
            detected_tools=c.detected_tools,
            detected_format=c.detected_format,
            license_spdx=c.license_spdx,
            stars=c.stars,
            contact_email=c.contact_email,
            contact_name=c.contact_name,
            contact_channel=c.contact_channel,
            assigned_admin_id=c.assigned_admin_id,
            outreach_status=c.outreach_status,
            contacted_at=c.contacted_at,
            published_package_id=c.published_package_id,
            last_event_at=c.last_event_at,
            last_event_type=c.last_event_type,
            admin_notes=c.admin_notes,
            skip_reason=c.skip_reason,
            created_at=c.created_at,
            updated_at=c.updated_at,
            invite_code=invite_info[0] if invite_info else None,
            invite_status=invite_info[1] if invite_info else None,
            click_count=click_map.get(c.id, 0),
        ))

    return CandidateListResponse(items=items, total=total)


@admin_router.post("/candidates", response_model=CandidateResponse, status_code=201, dependencies=[Depends(rate_limit(30, 60))])
async def create_candidate(
    body: CandidateCreateRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Manually create a candidate."""
    # Check for duplicate (source + source_url is UNIQUE)
    dup_check = await session.execute(
        select(ImportCandidate.id).where(
            and_(
                ImportCandidate.source == body.source,
                ImportCandidate.source_url == body.source_url,
            )
        )
    )
    if dup_check.scalar_one_or_none():
        raise AppError("CANDIDATE_DUPLICATE", "A candidate with this source and URL already exists", 409)

    candidate = ImportCandidate(
        source=body.source,
        source_url=body.source_url,
        repo_owner=body.repo_owner,
        repo_name=body.repo_name,
        display_name=body.display_name,
        description=body.description,
        detected_tools=body.detected_tools,
        detected_format=body.detected_format,
        license_spdx=body.license_spdx,
        stars=body.stars,
        contact_email=body.contact_email,
        contact_name=body.contact_name,
        contact_channel=body.contact_channel,
        outreach_status="discovered",
    )
    session.add(candidate)
    await session.flush()

    await log_event(session, candidate.id, "candidate_discovered", {
        "source": body.source,
        "source_url": body.source_url,
    }, actor_user_id=user.id)
    await session.commit()
    await session.refresh(candidate)

    return CandidateResponse(
        id=candidate.id,
        source=candidate.source,
        source_url=candidate.source_url,
        repo_owner=candidate.repo_owner,
        repo_name=candidate.repo_name,
        display_name=candidate.display_name,
        description=candidate.description,
        detected_tools=candidate.detected_tools,
        detected_format=candidate.detected_format,
        license_spdx=candidate.license_spdx,
        stars=candidate.stars,
        contact_email=candidate.contact_email,
        contact_name=candidate.contact_name,
        contact_channel=candidate.contact_channel,
        assigned_admin_id=candidate.assigned_admin_id,
        outreach_status=candidate.outreach_status,
        contacted_at=candidate.contacted_at,
        published_package_id=candidate.published_package_id,
        last_event_at=candidate.last_event_at,
        last_event_type=candidate.last_event_type,
        admin_notes=candidate.admin_notes,
        skip_reason=candidate.skip_reason,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


@admin_router.put("/candidates/{candidate_id}", response_model=CandidateResponse, dependencies=[Depends(rate_limit(30, 60))])
async def update_candidate(
    candidate_id: UUID,
    body: CandidateUpdateRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Update candidate fields."""
    result = await session.execute(
        select(ImportCandidate).where(ImportCandidate.id == candidate_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate not found", 404)

    notes_changed = False
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "admin_notes" and value != candidate.admin_notes:
            notes_changed = True
        setattr(candidate, field, value)

    candidate.updated_at = datetime.now(timezone.utc)

    if notes_changed:
        await log_event(session, candidate_id, "note_added", {
            "notes_preview": (body.admin_notes or "")[:100],
        }, actor_user_id=user.id)

    await session.commit()
    await session.refresh(candidate)

    return CandidateResponse(
        id=candidate.id,
        source=candidate.source,
        source_url=candidate.source_url,
        repo_owner=candidate.repo_owner,
        repo_name=candidate.repo_name,
        display_name=candidate.display_name,
        description=candidate.description,
        detected_tools=candidate.detected_tools,
        detected_format=candidate.detected_format,
        license_spdx=candidate.license_spdx,
        stars=candidate.stars,
        contact_email=candidate.contact_email,
        contact_name=candidate.contact_name,
        contact_channel=candidate.contact_channel,
        assigned_admin_id=candidate.assigned_admin_id,
        outreach_status=candidate.outreach_status,
        contacted_at=candidate.contacted_at,
        published_package_id=candidate.published_package_id,
        last_event_at=candidate.last_event_at,
        last_event_type=candidate.last_event_type,
        admin_notes=candidate.admin_notes,
        skip_reason=candidate.skip_reason,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


@admin_router.post("/candidates/{candidate_id}/invite", response_model=InviteGenerateResponse, dependencies=[Depends(rate_limit(20, 60))])
async def generate_invite(
    candidate_id: UUID,
    request: Request,
    body: InviteGenerateRequest | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Generate invite code for a candidate. Optionally send outreach email."""
    frontend_url = getattr(settings, "FRONTEND_URL", "https://agentnode.net")

    invite = await create_invite_for_candidate(session, candidate_id, user.id, frontend_url)

    tracking_url = f"{frontend_url}/i/{invite.code}"
    email_sent = False

    # Auto-send outreach email if requested
    if body and body.send_email and invite.candidate_id:
        result = await session.execute(
            select(ImportCandidate).where(ImportCandidate.id == invite.candidate_id)
        )
        candidate = result.scalar_one_or_none()
        if candidate and candidate.contact_email:
            from app.shared.email import send_invite_outreach_email
            email_sent = await send_invite_outreach_email(
                to=candidate.contact_email,
                contact_name=candidate.contact_name,
                display_name=candidate.display_name or candidate.repo_name or "your tool",
                description=candidate.description,
                source_url=candidate.source_url,
                tracking_url=tracking_url,
            )
            if email_sent:
                await log_event(session, candidate_id, "email_sent", {
                    "subject": f"Publish {candidate.display_name or candidate.repo_name} on AgentNode",
                    "channel": "email",
                    "to": candidate.contact_email,
                    "auto": True,
                }, actor_user_id=user.id)

    await session.commit()

    return InviteGenerateResponse(
        id=invite.id,
        code=invite.code,
        invite_url=f"{frontend_url}/invite/{invite.code}",
        tracking_url=tracking_url,
        expires_at=invite.expires_at,
        email_sent=email_sent,
    )


@admin_router.post("/candidates/{candidate_id}/email-sent", dependencies=[Depends(rate_limit(30, 60))])
async def mark_email_sent(
    candidate_id: UUID,
    body: EmailSentRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Log that an email was sent to the candidate."""
    # Verify candidate exists
    result = await session.execute(
        select(ImportCandidate.id).where(ImportCandidate.id == candidate_id)
    )
    if not result.scalar_one_or_none():
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate not found", 404)

    await log_event(session, candidate_id, "email_sent", {
        "subject": body.subject,
        "channel": body.channel,
    }, actor_user_id=user.id)
    await session.commit()
    return {"ok": True}


@admin_router.get("/candidates/{candidate_id}/events", response_model=EventListResponse)
async def get_candidate_events(
    candidate_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Get event timeline for a candidate."""
    result = await session.execute(
        select(CandidateEvent)
        .where(CandidateEvent.candidate_id == candidate_id)
        .order_by(CandidateEvent.created_at.desc())
    )
    events = result.scalars().all()

    return EventListResponse(
        items=[
            EventResponse(
                id=e.id,
                candidate_id=e.candidate_id,
                event_type=e.event_type,
                metadata=e.metadata_,
                actor_user_id=e.actor_user_id,
                created_at=e.created_at,
            )
            for e in events
        ]
    )


@admin_router.get("/invites", response_model=InviteListResponse)
async def list_invites(
    status: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all invite codes."""
    query = select(InviteCode).order_by(InviteCode.created_at.desc())
    if status:
        query = query.where(InviteCode.status == status)

    result = await session.execute(query)
    invites = result.scalars().all()

    count_result = await session.execute(
        select(func.count(InviteCode.id)).where(InviteCode.status == status) if status
        else select(func.count(InviteCode.id))
    )
    total = count_result.scalar() or 0

    return InviteListResponse(
        items=[
            InviteAdminResponse(
                id=i.id,
                code=i.code,
                candidate_id=i.candidate_id,
                status=i.status,
                claimed_by_user_id=i.claimed_by_user_id,
                expires_at=i.expires_at,
                created_at=i.created_at,
            )
            for i in invites
        ],
        total=total,
    )


@admin_router.delete("/invites/{invite_id}", dependencies=[Depends(rate_limit(20, 60))])
async def revoke_invite(
    invite_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Revoke an invite."""
    result = await session.execute(
        select(InviteCode).where(InviteCode.id == invite_id)
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise AppError("INVITE_NOT_FOUND", "Invite not found", 404)

    if invite.status != "active":
        raise AppError("INVITE_NOT_ACTIVE", "Invite is not active", 400)

    invite.status = "revoked"

    if invite.candidate_id:
        await log_event(session, invite.candidate_id, "invite_revoked", {
            "invite_code": invite.code,
        }, actor_user_id=user.id)

    await session.commit()
    return {"ok": True}


@admin_router.post("/candidates/bulk-send", response_model=BulkSendResponse, dependencies=[Depends(rate_limit(5, 60))])
async def bulk_send_invites(
    body: BulkSendRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Bulk-generate invites and optionally send outreach emails.

    Only targets candidates that are 'discovered' (never contacted),
    have a contact_email, and have no active invite.
    """
    frontend_url = getattr(settings, "FRONTEND_URL", "https://agentnode.net")

    # Build query for eligible candidates
    conditions = [
        ImportCandidate.outreach_status == "discovered",
        ImportCandidate.contact_email.isnot(None),
        ImportCandidate.contact_email != "",
    ]
    if body.min_stars > 0:
        conditions.append(ImportCandidate.stars >= body.min_stars)
    if body.source:
        conditions.append(ImportCandidate.source == body.source)
    if body.detected_format:
        conditions.append(ImportCandidate.detected_format == body.detected_format)

    # Exclude candidates that already have an active invite
    active_invite_ids = select(InviteCode.candidate_id).where(InviteCode.status == "active").distinct()
    conditions.append(ImportCandidate.id.notin_(active_invite_ids))

    query = (
        select(ImportCandidate)
        .where(and_(*conditions))
        .order_by(ImportCandidate.stars.desc().nulls_last())
        .limit(body.limit)
    )

    result = await session.execute(query)
    candidates = result.scalars().all()

    # Dry run: just return preview, no DB changes
    if not body.send_email:
        report = [
            {
                "display_name": c.display_name or c.repo_name,
                "contact_email": c.contact_email,
                "stars": c.stars,
                "tracking_url": None,
                "status": "preview",
            }
            for c in candidates if c.contact_email
        ]
        return BulkSendResponse(
            invites_created=0,
            emails_sent=0,
            emails_failed=0,
            skipped_no_email=sum(1 for c in candidates if not c.contact_email),
            candidates=report,
        )

    # Real send: generate invites + send emails
    invites_created = 0
    emails_sent = 0
    emails_failed = 0
    skipped_no_email = 0
    report: list[dict] = []

    for candidate in candidates:
        if not candidate.contact_email:
            skipped_no_email += 1
            continue

        # Generate invite
        invite = await create_invite_for_candidate(session, candidate.id, user.id, frontend_url)
        invites_created += 1
        tracking_url = f"{frontend_url}/i/{invite.code}"

        from app.shared.email import send_invite_outreach_email
        sent = await send_invite_outreach_email(
            to=candidate.contact_email,
            contact_name=candidate.contact_name,
            display_name=candidate.display_name or candidate.repo_name or "your tool",
            description=candidate.description,
            source_url=candidate.source_url,
            tracking_url=tracking_url,
        )

        entry = {
            "display_name": candidate.display_name or candidate.repo_name,
            "contact_email": candidate.contact_email,
            "stars": candidate.stars,
            "tracking_url": tracking_url,
            "status": "email_sent" if sent else "email_failed",
        }

        if sent:
            emails_sent += 1
            await log_event(session, candidate.id, "email_sent", {
                "subject": f"Publish {candidate.display_name or candidate.repo_name} on AgentNode",
                "channel": "email",
                "to": candidate.contact_email,
                "auto": True,
                "bulk": True,
            }, actor_user_id=user.id)
        else:
            emails_failed += 1

        report.append(entry)

    await session.commit()

    return BulkSendResponse(
        invites_created=invites_created,
        emails_sent=emails_sent,
        emails_failed=emails_failed,
        skipped_no_email=skipped_no_email,
        candidates=report,
    )


@admin_router.post("/candidates/{candidate_id}/followup", dependencies=[Depends(rate_limit(20, 60))])
async def send_followup(
    candidate_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Send a follow-up email to a candidate who clicked but didn't sign up."""
    frontend_url = getattr(settings, "FRONTEND_URL", "https://agentnode.net")

    result = await session.execute(
        select(ImportCandidate).where(ImportCandidate.id == candidate_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate not found", 404)

    if not candidate.contact_email:
        raise AppError("NO_EMAIL", "Candidate has no contact email", 400)

    # Get active invite for tracking URL
    invite_result = await session.execute(
        select(InviteCode).where(
            and_(
                InviteCode.candidate_id == candidate_id,
                InviteCode.status == "active",
            )
        )
    )
    invite = invite_result.scalar_one_or_none()
    if not invite:
        raise AppError("NO_ACTIVE_INVITE", "No active invite for this candidate. Generate one first.", 400)

    tracking_url = f"{frontend_url}/i/{invite.code}"

    from app.shared.email import send_invite_followup_email
    sent = await send_invite_followup_email(
        to=candidate.contact_email,
        contact_name=candidate.contact_name,
        display_name=candidate.display_name or candidate.repo_name or "your tool",
        tracking_url=tracking_url,
    )

    if sent:
        await log_event(session, candidate_id, "followup_sent", {
            "to": candidate.contact_email,
            "auto": True,
        }, actor_user_id=user.id)
        await session.commit()

    return {"ok": True, "email_sent": sent}


@admin_router.post("/candidates/auto-followup", dependencies=[Depends(rate_limit(5, 60))])
async def auto_followup(
    days: int = Query(default=5, ge=1, le=30),
    limit: int = Query(default=50, ge=1, le=200),
    dry_run: bool = Query(default=True),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Auto-send follow-up emails to candidates contacted X days ago who haven't clicked.

    Only targets candidates that:
    - Have outreach_status 'contacted' (email sent, but no click)
    - Were contacted at least `days` days ago
    - Have a contact_email
    - Have an active invite
    - Have NOT already received a followup_sent event
    """
    from datetime import timedelta

    frontend_url = getattr(settings, "FRONTEND_URL", "https://agentnode.net")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Find candidates who were contacted but didn't click, and haven't received a follow-up
    followup_already = (
        select(CandidateEvent.candidate_id)
        .where(CandidateEvent.event_type == "followup_sent")
        .distinct()
    )
    clicked_already = (
        select(CandidateEvent.candidate_id)
        .where(CandidateEvent.event_type == "invite_link_clicked")
        .distinct()
    )

    query = (
        select(ImportCandidate)
        .where(and_(
            ImportCandidate.outreach_status == "contacted",
            ImportCandidate.contacted_at.isnot(None),
            ImportCandidate.contacted_at <= cutoff,
            ImportCandidate.contact_email.isnot(None),
            ImportCandidate.contact_email != "",
            ImportCandidate.id.notin_(followup_already),
            ImportCandidate.id.notin_(clicked_already),
        ))
        .order_by(ImportCandidate.stars.desc().nulls_last())
        .limit(limit)
    )

    result = await session.execute(query)
    candidates = result.scalars().all()

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    report: list[dict] = []

    for candidate in candidates:
        # Get active invite
        invite_result = await session.execute(
            select(InviteCode).where(and_(
                InviteCode.candidate_id == candidate.id,
                InviteCode.status == "active",
            ))
        )
        invite = invite_result.scalar_one_or_none()
        if not invite:
            skipped_count += 1
            report.append({"display_name": candidate.display_name, "status": "skipped_no_invite"})
            continue

        tracking_url = f"{frontend_url}/i/{invite.code}"

        if dry_run:
            report.append({
                "display_name": candidate.display_name or candidate.repo_name,
                "contact_email": candidate.contact_email,
                "stars": candidate.stars,
                "contacted_at": str(candidate.contacted_at),
                "status": "would_send",
            })
            continue

        from app.shared.email import send_invite_followup_email
        ok = await send_invite_followup_email(
            to=candidate.contact_email,
            contact_name=candidate.contact_name,
            display_name=candidate.display_name or candidate.repo_name or "your tool",
            tracking_url=tracking_url,
        )

        if ok:
            sent_count += 1
            await log_event(session, candidate.id, "followup_sent", {
                "to": candidate.contact_email,
                "auto": True,
                "days_since_contact": days,
            }, actor_user_id=user.id)
            report.append({"display_name": candidate.display_name, "status": "sent"})
        else:
            failed_count += 1
            report.append({"display_name": candidate.display_name, "status": "failed"})

    if not dry_run:
        await session.commit()

    return {
        "dry_run": dry_run,
        "eligible": len(candidates),
        "sent": sent_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "candidates": report,
    }
