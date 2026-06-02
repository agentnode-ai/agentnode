"""Add mcp_submissions table for MCP submit flow.

Revision ID: 037
Revises: 036
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("publisher_id", UUID(as_uuid=True), sa.ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_name", sa.VARCHAR(200), nullable=False),
        sa.Column("package_registry", sa.VARCHAR(20), nullable=False),
        sa.Column("package_version", sa.VARCHAR(50)),
        sa.Column("source_repo", sa.Text),
        sa.Column("manifest_raw", JSONB, nullable=False),
        sa.Column("verification_report", JSONB, nullable=False),
        sa.Column("status", sa.VARCHAR(40), nullable=False, server_default="pending"),
        sa.Column("reviewer_notes", sa.Text),
        sa.Column("reviewed_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", TIMESTAMP(timezone=True)),
        sa.Column("published_package_id", UUID(as_uuid=True), sa.ForeignKey("packages.id", ondelete="SET NULL")),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mcp_submissions_status", "mcp_submissions", ["status"])
    op.create_index("ix_mcp_submissions_publisher", "mcp_submissions", ["publisher_id"])


def downgrade() -> None:
    op.drop_table("mcp_submissions")
