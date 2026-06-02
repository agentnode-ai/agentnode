"""Add publisher_package_claims table (ownership / package_control axis).

Proof that a publisher controls a specific npm/PyPI package. Kept strictly
separate from server_verification.repo_consistency. Step 1: manual_admin only.

Revision ID: 040
Revises: 039
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publisher_package_claims",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("publisher_id", UUID(as_uuid=True),
                  sa.ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("registry", sa.VARCHAR(20), nullable=False),
        sa.Column("package_name", sa.VARCHAR(200), nullable=False),
        sa.Column("package_name_normalized", sa.VARCHAR(200), nullable=False),
        sa.Column("method", sa.VARCHAR(30), nullable=False),
        sa.Column("strength", sa.VARCHAR(20), nullable=False),
        sa.Column("status", sa.VARCHAR(20), nullable=False, server_default="verified"),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column("challenge_token_hash", sa.VARCHAR(128), nullable=True),
        sa.Column("verified_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verified_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ppc_lookup", "publisher_package_claims",
        ["publisher_id", "registry", "package_name_normalized"],
    )


def downgrade() -> None:
    op.drop_index("ix_ppc_lookup", table_name="publisher_package_claims")
    op.drop_table("publisher_package_claims")
