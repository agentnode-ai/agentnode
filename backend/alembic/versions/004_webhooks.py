"""Add webhooks and webhook_deliveries tables.

Revision ID: 004
Revises: 003
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE webhooks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            publisher_id UUID NOT NULL REFERENCES publishers(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            secret TEXT,
            events JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_webhooks_publisher ON webhooks(publisher_id)")

    op.execute("""
        CREATE TABLE webhook_deliveries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            webhook_id UUID NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            status_code TEXT,
            success BOOLEAN NOT NULL DEFAULT false,
            delivered_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_deliveries_webhook ON webhook_deliveries(webhook_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS webhook_deliveries")
    op.execute("DROP TABLE IF EXISTS webhooks")
