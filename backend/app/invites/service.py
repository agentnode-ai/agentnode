from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import json

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.invites.models import CandidateEvent, ImportCandidate, InviteCode
from app.shared.exceptions import AppError

INVITE_TTL_DAYS = 30


# ── Event Logging ──

async def log_event(
    session: AsyncSession,
    candidate_id: UUID,
    event_type: str,
    metadata: dict | None = None,
    actor_user_id: UUID | None = None,
) -> CandidateEvent:
    now = datetime.now(timezone.utc)
    event = CandidateEvent(
        candidate_id=candidate_id,
        event_type=event_type,
        metadata_=metadata or {},
        actor_user_id=actor_user_id,
        created_at=now,
    )
    session.add(event)

    # Update cached event fields on candidate
    await session.execute(
        update(ImportCandidate)
        .where(ImportCandidate.id == candidate_id)
        .values(last_event_at=now, last_event_type=event_type, updated_at=now)
    )

    return event


# ── Status derivation ──

STATUS_PRIORITY = [
    ("published", "package_published"),
    ("signed_up", "invite_claimed"),
    ("engaged", "invite_link_clicked"),
    ("contacted", "invite_created"),
    ("contacted", "email_sent"),
]


async def derive_outreach_status(session: AsyncSession, candidate_id: UUID) -> str:
    result = await session.execute(
        select(CandidateEvent.event_type)
        .where(CandidateEvent.candidate_id == candidate_id)
    )
    event_types = {row[0] for row in result.all()}

    for status, event_type in STATUS_PRIORITY:
        if event_type in event_types:
            return status
    return "discovered"


# ── Invite code generation ──

def generate_invite_code() -> str:
    return secrets.token_urlsafe(20)


# ── Invite queries ──

async def get_invite_by_code(session: AsyncSession, code: str) -> InviteCode | None:
    result = await session.execute(
        select(InviteCode).where(InviteCode.code == code)
    )
    return result.scalar_one_or_none()


async def get_active_invite_for_candidate(session: AsyncSession, candidate_id: UUID) -> InviteCode | None:
    result = await session.execute(
        select(InviteCode).where(
            and_(
                InviteCode.candidate_id == candidate_id,
                InviteCode.status == "active",
            )
        )
    )
    return result.scalar_one_or_none()


# ── Claim ──

async def claim_invite(
    session: AsyncSession,
    code: str,
    user_id: UUID,
) -> dict:
    # Atomic: SELECT FOR UPDATE
    result = await session.execute(
        select(InviteCode)
        .where(InviteCode.code == code)
        .with_for_update()
    )
    invite = result.scalar_one_or_none()

    if not invite:
        raise AppError("INVITE_NOT_FOUND", "Invite code not found", 404)

    # Already claimed by same user → idempotent 200
    if invite.status == "claimed" and invite.claimed_by_user_id == user_id:
        return invite.prefill_data or {}

    # Already claimed by different user → 409
    if invite.status == "claimed":
        raise AppError("INVITE_ALREADY_CLAIMED", "This invite has already been claimed", 409)

    # Expired or revoked
    if invite.status in ("expired", "revoked"):
        raise AppError("INVITE_INVALID", "This invite is no longer valid", 410)

    # Check TTL
    if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
        invite.status = "expired"
        raise AppError("INVITE_EXPIRED", "This invite has expired", 410)

    # Claim
    invite.claimed_by_user_id = user_id
    invite.status = "claimed"

    # Update candidate status
    if invite.candidate_id:
        await session.execute(
            update(ImportCandidate)
            .where(ImportCandidate.id == invite.candidate_id)
            .values(outreach_status="signed_up", updated_at=datetime.now(timezone.utc))
        )
        await log_event(session, invite.candidate_id, "invite_claimed", {"invite_code": code}, actor_user_id=user_id)

    return invite.prefill_data or {}


# ── Build prefill from candidate ──

def build_prefill_from_candidate(candidate: ImportCandidate) -> dict:
    tools = candidate.detected_tools or []
    manifest_data = {
        "anp_version": "0.2",
        "package_id": f"{candidate.repo_owner or 'unknown'}/{candidate.repo_name or 'tool'}",
        "version": "0.1.0",
        "name": candidate.display_name or candidate.repo_name or "Untitled Tool",
        "summary": (candidate.description or "")[:140] or "Imported tool",
        "description": candidate.description or "",
    }
    if tools:
        manifest_data["capabilities"] = {
            "tools": [
                {
                    "name": t.get("name", "tool"),
                    "description": t.get("description", ""),
                    **({"capability_id": t["capability_id"]} if t.get("capability_id") else {}),
                }
                for t in tools
            ]
        }

    manifest_text = json.dumps(manifest_data, indent=2)

    return {
        "source": "import",
        "manifestText": manifest_text,
        "sourceUrl": candidate.source_url,
        "detectedFormat": candidate.detected_format,
    }


# ── Create invite for candidate ──

async def create_invite_for_candidate(
    session: AsyncSession,
    candidate_id: UUID,
    actor_user_id: UUID,
    base_url: str = "https://agentnode.net",
) -> InviteCode:
    # Load candidate
    result = await session.execute(
        select(ImportCandidate).where(ImportCandidate.id == candidate_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate not found", 404)

    # Revoke existing active invite (max 1)
    existing = await get_active_invite_for_candidate(session, candidate_id)
    if existing:
        existing.status = "revoked"
        await log_event(session, candidate_id, "invite_revoked", {"old_code": existing.code}, actor_user_id=actor_user_id)

    # Generate new invite
    code = generate_invite_code()
    now = datetime.now(timezone.utc)
    prefill = build_prefill_from_candidate(candidate)

    invite = InviteCode(
        code=code,
        candidate_id=candidate_id,
        prefill_data=prefill,
        status="active",
        expires_at=now + timedelta(days=INVITE_TTL_DAYS),
        created_at=now,
    )
    session.add(invite)

    # Update candidate
    candidate.outreach_status = "contacted"
    candidate.contacted_at = now
    candidate.updated_at = now

    await log_event(session, candidate_id, "invite_created", {"invite_code": code}, actor_user_id=actor_user_id)

    await session.flush()

    return invite


# ── Mark published ──

async def mark_candidate_published(
    session: AsyncSession,
    candidate_id: UUID,
    package_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ImportCandidate)
        .where(ImportCandidate.id == candidate_id)
        .values(
            outreach_status="published",
            published_package_id=package_id,
            updated_at=now,
        )
    )
    await log_event(session, candidate_id, "package_published", {"package_id": str(package_id)})


# ── Funnel aggregation ──

async def get_funnel_stats(session: AsyncSession) -> dict:
    # Count candidates by outreach_status
    result = await session.execute(
        select(ImportCandidate.outreach_status, func.count(ImportCandidate.id))
        .group_by(ImportCandidate.outreach_status)
    )
    status_counts = dict(result.all())

    # Count verified from events (not an outreach_status)
    verified_result = await session.execute(
        select(func.count(func.distinct(CandidateEvent.candidate_id)))
        .where(CandidateEvent.event_type == "verification_passed")
    )
    verified = verified_result.scalar() or 0

    # Count engaged from events (click without signup)
    engaged_result = await session.execute(
        select(func.count(func.distinct(CandidateEvent.candidate_id)))
        .where(CandidateEvent.event_type == "invite_link_clicked")
    )
    engaged = engaged_result.scalar() or 0

    discovered = sum(status_counts.values())  # total candidates
    contacted = status_counts.get("contacted", 0) + status_counts.get("engaged", 0) + status_counts.get("signed_up", 0) + status_counts.get("published", 0)
    signed_up = status_counts.get("signed_up", 0) + status_counts.get("published", 0)
    published = status_counts.get("published", 0)

    def rate(num: int, denom: int) -> float:
        return round(num / denom * 100, 1) if denom > 0 else 0.0

    return {
        "discovered": discovered,
        "contacted": contacted,
        "engaged": engaged,
        "signed_up": signed_up,
        "published": published,
        "verified": verified,
        "conversion_rates": {
            "contacted_to_clicked": rate(engaged, contacted),
            "clicked_to_signed_up": rate(signed_up, engaged),
            "signed_up_to_published": rate(published, signed_up),
            "published_to_verified": rate(verified, published),
        },
    }
