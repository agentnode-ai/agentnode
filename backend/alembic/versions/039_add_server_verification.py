"""Add server_verification column to mcp_submissions.

Backend-derived registry facts (package_exists, version_exists, repo_consistency,
resolved_version, ...). Authoritative for the publish gate; the client-supplied
verification_report becomes advisory/maintainer-attested.

Revision ID: 039
Revises: 038
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_submissions", sa.Column("server_verification", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_submissions", "server_verification")
