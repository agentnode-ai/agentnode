"""Add missing performance indexes for Sprint G.

P1-D2: package_reports.package_id had no index, forcing a seq scan on every
       admin "reports for package X" query.
P1-D9: support_tickets.category had no index, forcing a seq scan on every
       admin filter.

Both indexes are safe to add concurrently in production — they are pure
read-side speedups, no application change required.

Revision ID: 028
Revises: 027
"""
from alembic import op
from sqlalchemy import text

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_package_reports_package_id "
        "ON package_reports (package_id)"
    ))

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_category "
        "ON support_tickets (category)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX IF EXISTS ix_support_tickets_category"))
    conn.execute(text("DROP INDEX IF EXISTS ix_package_reports_package_id"))
