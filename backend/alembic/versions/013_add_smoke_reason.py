"""Add smoke_reason column to verification_results.

Revision ID: 013
Revises: 012
"""
from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("verification_results", sa.Column("smoke_reason", sa.Text(), nullable=True))
    op.create_index("ix_verification_results_smoke_reason", "verification_results", ["smoke_reason"])


def downgrade() -> None:
    op.drop_index("ix_verification_results_smoke_reason", table_name="verification_results")
    op.drop_column("verification_results", "smoke_reason")
