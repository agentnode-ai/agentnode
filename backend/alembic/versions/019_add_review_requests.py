"""Add review_requests, processed_stripe_events, and review badge columns on package_versions.

Revision ID: 019
Revises: 018
"""
from alembic import op


revision = "019"
down_revision = "018"


def upgrade() -> None:
    # --- Enums ---
    op.execute("CREATE TYPE review_tier AS ENUM ('security', 'compatibility', 'full')")
    op.execute("""
        CREATE TYPE review_request_status AS ENUM (
            'pending_payment', 'paid', 'in_review', 'approved',
            'changes_requested', 'rejected', 'refunded'
        )
    """)

    # --- review_requests ---
    op.execute("""
        CREATE TABLE review_requests (
            id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            order_id                   TEXT UNIQUE NOT NULL,
            publisher_id               UUID NOT NULL REFERENCES publishers(id) ON DELETE CASCADE,
            package_id                 UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
            package_version_id         UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            tier                       review_tier NOT NULL,
            express                    BOOLEAN NOT NULL DEFAULT FALSE,
            price_cents                INTEGER NOT NULL,
            currency                   TEXT NOT NULL DEFAULT 'usd',
            status                     review_request_status NOT NULL DEFAULT 'pending_payment',
            stripe_checkout_session_id TEXT UNIQUE,
            stripe_payment_intent_id   TEXT UNIQUE,
            paid_at                    TIMESTAMPTZ,
            assigned_reviewer_id       UUID REFERENCES users(id) ON DELETE SET NULL,
            review_notes               TEXT,
            review_result              JSONB,
            refund_amount_cents        INTEGER,
            reviewed_at                TIMESTAMPTZ,
            created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("CREATE INDEX idx_review_requests_publisher ON review_requests(publisher_id)")
    op.execute("CREATE INDEX idx_review_requests_package ON review_requests(package_id)")
    op.execute("CREATE INDEX idx_review_requests_version ON review_requests(package_version_id)")
    op.execute("CREATE INDEX idx_review_requests_status ON review_requests(status)")
    op.execute("CREATE INDEX idx_review_requests_order ON review_requests(order_id)")

    # --- processed_stripe_events ---
    op.execute("""
        CREATE TABLE processed_stripe_events (
            event_id     TEXT PRIMARY KEY,
            event_type   TEXT NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- Materialized review badges on package_versions ---
    op.execute("ALTER TABLE package_versions ADD COLUMN security_reviewed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE package_versions ADD COLUMN compatibility_reviewed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE package_versions ADD COLUMN manually_reviewed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS manually_reviewed_at")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS compatibility_reviewed_at")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS security_reviewed_at")
    op.execute("DROP TABLE IF EXISTS processed_stripe_events")
    op.execute("DROP TABLE IF EXISTS review_requests")
    op.execute("DROP TYPE IF EXISTS review_request_status")
    op.execute("DROP TYPE IF EXISTS review_tier")
