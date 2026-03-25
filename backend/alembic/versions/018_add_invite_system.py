"""Add invite system: import_candidates, invite_codes, candidate_events.

Revision ID: 018
Revises: 017
"""
from alembic import op
import sqlalchemy as sa


revision = "018"
down_revision = "017"


def upgrade() -> None:
    # --- import_candidates ---
    op.execute("""
        CREATE TABLE import_candidates (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source          VARCHAR(50) NOT NULL,
            source_url      TEXT NOT NULL,
            repo_owner      VARCHAR(100),
            repo_name       VARCHAR(100),
            display_name    TEXT,
            description     TEXT,
            detected_tools  JSONB,
            detected_format VARCHAR(20),
            license_spdx    TEXT,
            stars           INTEGER,

            contact_email   TEXT,
            contact_name    TEXT,
            contact_channel VARCHAR(20),
            assigned_admin_id UUID REFERENCES users(id) ON DELETE SET NULL,

            outreach_status VARCHAR(20) NOT NULL DEFAULT 'discovered',
            contacted_at    TIMESTAMPTZ,
            published_package_id UUID REFERENCES packages(id) ON DELETE SET NULL,

            last_event_at   TIMESTAMPTZ,
            last_event_type VARCHAR(50),

            admin_notes     TEXT,
            skip_reason     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            UNIQUE(source, source_url)
        )
    """)

    op.execute("""
        CREATE INDEX idx_candidates_outreach_status ON import_candidates(outreach_status)
    """)
    op.execute("""
        CREATE INDEX idx_candidates_source ON import_candidates(source)
    """)
    op.execute("""
        CREATE INDEX idx_candidates_assigned_admin ON import_candidates(assigned_admin_id)
    """)

    # --- invite_codes ---
    op.execute("""
        CREATE TABLE invite_codes (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code               VARCHAR(40) UNIQUE NOT NULL,
            candidate_id       UUID REFERENCES import_candidates(id) ON DELETE SET NULL,
            prefill_data       JSONB,
            claimed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            status             VARCHAR(20) NOT NULL DEFAULT 'active',
            expires_at         TIMESTAMPTZ,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_invite_codes_code ON invite_codes(code)
    """)
    op.execute("""
        CREATE INDEX idx_invite_codes_candidate ON invite_codes(candidate_id)
    """)
    op.execute("""
        CREATE INDEX idx_invite_codes_status ON invite_codes(status)
    """)

    # --- candidate_events ---
    op.execute("""
        CREATE TABLE candidate_events (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            candidate_id   UUID NOT NULL REFERENCES import_candidates(id) ON DELETE CASCADE,
            event_type     VARCHAR(50) NOT NULL,
            metadata       JSONB,
            actor_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX idx_events_candidate ON candidate_events(candidate_id, created_at)
    """)
    op.execute("""
        CREATE INDEX idx_events_type ON candidate_events(event_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS candidate_events")
    op.execute("DROP TABLE IF EXISTS invite_codes")
    op.execute("DROP TABLE IF EXISTS import_candidates")
