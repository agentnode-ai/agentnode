"""Add publishers.trust_auto_granted_at.

Set when the platform itself promoted a publisher to 'verified' (confirmed
email + a skill that passed verification with a clean content check). Lets the
automation withdraw only what it granted: an admin-set trust level clears the
column and is never touched by the automation.

Revision ID: 042
Revises: 041
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publishers",
        sa.Column("trust_auto_granted_at", TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publishers", "trust_auto_granted_at")
