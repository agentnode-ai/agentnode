"""Add environment_info, verification_mode, contract columns for Phase 5-6.

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Phase 5B: Environment info (capabilities, python version, sandbox mode)
    op.add_column(
        "verification_results",
        sa.Column("environment_info", JSONB(), nullable=True),
    )

    # Phase 6E: verification_mode (real/mock/limited)
    op.add_column(
        "verification_results",
        sa.Column("verification_mode", sa.Text(), nullable=True),
    )

    # Phase 6A: Contract validation details
    op.add_column(
        "verification_results",
        sa.Column("contract_details", JSONB(), nullable=True),
    )

    # Phase 6B: Confidence level (high/medium/low)
    op.add_column(
        "verification_results",
        sa.Column("confidence", sa.Text(), nullable=True),
    )

    # Phase 7A: Smoke confidence (high/medium for credential boundary)
    op.add_column(
        "verification_results",
        sa.Column("smoke_confidence", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("verification_results", "smoke_confidence")
    op.drop_column("verification_results", "confidence")
    op.drop_column("verification_results", "contract_details")
    op.drop_column("verification_results", "verification_mode")
    op.drop_column("verification_results", "environment_info")
