"""Add tests_execution_mode to verification_results.

Tracks how publisher tests were executed:
container, skipped_no_container, auto_generated, not_present.

Revision ID: 032
Revises: 031
"""
from alembic import op
import sqlalchemy as sa


revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "verification_results",
        sa.Column("tests_execution_mode", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("verification_results", "tests_execution_mode")
