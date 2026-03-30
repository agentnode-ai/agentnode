from sqlalchemy import Boolean, Column, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.shared.models import Base, UUIDPrimaryKeyMixin


class ReviewRequest(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "review_requests"

    order_id = Column(Text, unique=True, nullable=False)
    publisher_id = Column(UUID(as_uuid=True), ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False, index=True)
    package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id", ondelete="CASCADE"), nullable=False, index=True)
    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    tier = Column(
        Enum("security", "compatibility", "full", name="review_tier", create_type=False),
        nullable=False,
    )
    express = Column(Boolean, nullable=False, default=False, server_default="false")
    price_cents = Column(Integer, nullable=False)
    currency = Column(Text, nullable=False, default="usd", server_default="'usd'")
    status = Column(
        Enum(
            "pending_payment", "paid", "in_review", "approved",
            "changes_requested", "rejected", "refunded",
            name="review_request_status", create_type=False,
        ),
        nullable=False,
        default="pending_payment",
    )
    stripe_checkout_session_id = Column(Text, unique=True, nullable=True)
    stripe_payment_intent_id = Column(Text, unique=True, nullable=True)
    paid_at = Column(TIMESTAMP(timezone=True), nullable=True)
    assigned_reviewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_notes = Column(Text, nullable=True)
    review_result = Column(JSONB, nullable=True)
    refund_amount_cents = Column(Integer, nullable=True)
    reviewed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")


class ProcessedStripeEvent(Base):
    __tablename__ = "processed_stripe_events"

    event_id = Column(Text, primary_key=True)
    event_type = Column(Text, nullable=False)
    processed_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
