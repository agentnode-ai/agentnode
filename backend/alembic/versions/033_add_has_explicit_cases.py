"""Add has_explicit_cases to verification_results.

Tracks whether the publisher provided explicit verification cases/fixtures/test_input.
Used by the Gold gate: packages without explicit cases cannot reach Gold tier.

Revision ID: 033
Revises: 032
"""
from alembic import op
import sqlalchemy as sa


revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "verification_results",
        sa.Column("has_explicit_cases", sa.Boolean(), nullable=True, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("verification_results", "has_explicit_cases")
