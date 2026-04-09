from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# --- Request bodies ---

class CreateReviewRequestBody(BaseModel):
    package_slug: str = Field(..., min_length=1, max_length=200)
    version: str = Field(..., min_length=1, max_length=100)
    tier: str = Field(..., pattern=r"^(security|compatibility|full)$")
    express: bool = False


class CompleteReviewBody(BaseModel):
    outcome: str = Field(..., pattern=r"^(approved|changes_requested|rejected)$")
    notes: str | None = None
    review_result: dict = Field(
        ...,
        description="Structured review result with security_passed, compatibility_passed, etc.",
    )


class AssignReviewerBody(BaseModel):
    reviewer_id: UUID


class RefundReviewBody(BaseModel):
    amount_cents: int | None = Field(None, ge=1, description="Partial refund amount; omit for full refund")
    reason: str = Field(..., min_length=1, max_length=500)


# --- Responses ---

class ReviewCheckoutResponse(BaseModel):
    review_id: UUID
    order_id: str
    checkout_url: str
    price_cents: int
    tier: str
    express: bool


class ReviewRequestResponse(BaseModel):
    id: UUID
    order_id: str
    package_slug: str | None = None
    package_name: str | None = None
    version: str | None = None
    tier: str
    express: bool
    price_cents: int
    currency: str
    status: str
    review_notes: str | None = None
    review_result: dict | None = None
    refund_amount_cents: int | None = None
    paid_at: datetime | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class AdminQueueItem(ReviewRequestResponse):
    publisher_slug: str | None = None
    publisher_name: str | None = None
    assigned_reviewer_id: UUID | None = None
    verification_status: str | None = None
    verification_tier: str | None = None
    verification_score: int | None = None
