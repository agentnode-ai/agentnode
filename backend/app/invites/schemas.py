from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ── Public ──

class InvitePublicResponse(BaseModel):
    code: str
    valid: bool
    display_name: str | None = None
    description: str | None = None
    source_url: str | None = None


class InviteClaimResponse(BaseModel):
    prefill_data: dict


# ── Admin: Candidates ──

class CandidateCreateRequest(BaseModel):
    source: str = Field(max_length=50)
    source_url: str
    repo_owner: str | None = None
    repo_name: str | None = None
    display_name: str | None = None
    description: str | None = None
    detected_tools: list[dict] | None = None
    detected_format: str | None = None
    license_spdx: str | None = None
    stars: int | None = None
    contact_email: str | None = None
    contact_name: str | None = None
    contact_channel: str | None = None


class CandidateUpdateRequest(BaseModel):
    outreach_status: str | None = None
    admin_notes: str | None = None
    contact_email: str | None = None
    contact_name: str | None = None
    contact_channel: str | None = None
    assigned_admin_id: UUID | None = None
    skip_reason: str | None = None


class CandidateResponse(BaseModel):
    id: UUID
    source: str
    source_url: str
    repo_owner: str | None = None
    repo_name: str | None = None
    display_name: str | None = None
    description: str | None = None
    detected_tools: list[dict] | None = None
    detected_format: str | None = None
    license_spdx: str | None = None
    stars: int | None = None
    contact_email: str | None = None
    contact_name: str | None = None
    contact_channel: str | None = None
    assigned_admin_id: UUID | None = None
    outreach_status: str
    contacted_at: datetime | None = None
    published_package_id: UUID | None = None
    last_event_at: datetime | None = None
    last_event_type: str | None = None
    admin_notes: str | None = None
    skip_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    # Joined fields (populated in list queries)
    invite_code: str | None = None
    invite_status: str | None = None
    click_count: int | None = None


class CandidateListResponse(BaseModel):
    items: list[CandidateResponse]
    total: int


# ── Admin: Invite generation ──

class InviteGenerateRequest(BaseModel):
    send_email: bool = False


class BulkSendRequest(BaseModel):
    """Bulk invite + email generation."""
    min_stars: int = 0
    source: str | None = None
    detected_format: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    send_email: bool = False  # False = dry run (generate invites only)


class BulkSendResponse(BaseModel):
    invites_created: int = 0
    emails_sent: int = 0
    emails_failed: int = 0
    skipped_no_email: int = 0
    candidates: list[dict] = []  # [{display_name, contact_email, status}]


# ── Admin: Invites ──

class InviteGenerateResponse(BaseModel):
    email_sent: bool = False
    id: UUID
    code: str
    invite_url: str
    tracking_url: str
    expires_at: datetime


class InviteAdminResponse(BaseModel):
    id: UUID
    code: str
    candidate_id: UUID | None = None
    status: str
    claimed_by_user_id: UUID | None = None
    expires_at: datetime | None = None
    created_at: datetime


class InviteListResponse(BaseModel):
    items: list[InviteAdminResponse]
    total: int


# ── Admin: Events ──

class EventResponse(BaseModel):
    id: UUID
    candidate_id: UUID
    event_type: str
    metadata: dict | None = None
    actor_user_id: UUID | None = None
    created_at: datetime


class EventListResponse(BaseModel):
    items: list[EventResponse]


# ── Admin: Email sent ──

class EmailSentRequest(BaseModel):
    subject: str | None = None
    channel: str | None = None


# ── Admin: Funnel ──

class FunnelResponse(BaseModel):
    discovered: int = 0
    contacted: int = 0
    engaged: int = 0
    signed_up: int = 0
    published: int = 0
    verified: int = 0
    conversion_rates: dict[str, float] = {}
