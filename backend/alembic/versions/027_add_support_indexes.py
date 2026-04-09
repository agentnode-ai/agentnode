"""Add performance indexes and CHECK constraints for support tables.

Revision ID: 027
Revises: 026
"""
from alembic import op
from sqlalchemy import text

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Index on support_messages.created_at for ORDER BY queries
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_support_messages_created_at "
        "ON support_messages (created_at)"
    ))

    # Composite index on (user_id, updated_at DESC) for user's sorted ticket list
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_user_updated "
        "ON support_tickets (user_id, updated_at DESC)"
    ))

    # CHECK constraint on status column
    conn.execute(text(
        "DO $$ BEGIN "
        "ALTER TABLE support_tickets ADD CONSTRAINT chk_support_ticket_status "
        "CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    ))

    # CHECK constraint on category column
    conn.execute(text(
        "DO $$ BEGIN "
        "ALTER TABLE support_tickets ADD CONSTRAINT chk_support_ticket_category "
        "CHECK (category IN ('account', 'publishing', 'reviews', 'billing', 'bug', 'other')); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE support_tickets DROP CONSTRAINT IF EXISTS chk_support_ticket_category"))
    conn.execute(text("ALTER TABLE support_tickets DROP CONSTRAINT IF EXISTS chk_support_ticket_status"))
    conn.execute(text("DROP INDEX IF EXISTS ix_support_tickets_user_updated"))
    conn.execute(text("DROP INDEX IF EXISTS ix_support_messages_created_at"))
