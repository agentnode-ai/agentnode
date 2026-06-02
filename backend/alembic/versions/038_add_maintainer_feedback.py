"""Add maintainer_feedback column to mcp_submissions.

Separates internal reviewer_notes (admin-only) from
maintainer_feedback (visible to the submitting publisher).

Revision ID: 038
Revises: 037
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_submissions", sa.Column("maintainer_feedback", sa.Text))


def downgrade() -> None:
    op.drop_column("mcp_submissions", "maintainer_feedback")
