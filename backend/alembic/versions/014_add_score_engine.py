"""Add score engine columns to verification_results and package_versions.

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # VerificationResult score fields
    op.add_column("verification_results", sa.Column("reliability", sa.Float(), nullable=True))
    op.add_column("verification_results", sa.Column("determinism_score", sa.Float(), nullable=True))
    op.add_column("verification_results", sa.Column("contract_valid", sa.Boolean(), nullable=True))
    op.add_column("verification_results", sa.Column("stability_log", JSONB(), nullable=True))
    op.add_column("verification_results", sa.Column("verification_score", sa.Integer(), nullable=True))
    op.add_column("verification_results", sa.Column("verification_tier", sa.Text(), nullable=True))
    op.add_column("verification_results", sa.Column("score_breakdown", JSONB(), nullable=True))
    op.add_column("verification_results", sa.Column("tests_auto_generated", sa.Boolean(), nullable=True))

    # PackageVersion denormalized score fields
    op.add_column("package_versions", sa.Column("verification_score", sa.Integer(), nullable=True))
    op.add_column("package_versions", sa.Column("verification_tier", sa.Text(), nullable=True))

    # Add 'scheduled' to verification_trigger enum
    op.execute("ALTER TYPE verification_trigger ADD VALUE IF NOT EXISTS 'scheduled'")


def downgrade() -> None:
    op.drop_column("package_versions", "verification_tier")
    op.drop_column("package_versions", "verification_score")

    op.drop_column("verification_results", "tests_auto_generated")
    op.drop_column("verification_results", "score_breakdown")
    op.drop_column("verification_results", "verification_tier")
    op.drop_column("verification_results", "verification_score")
    op.drop_column("verification_results", "stability_log")
    op.drop_column("verification_results", "contract_valid")
    op.drop_column("verification_results", "determinism_score")
    op.drop_column("verification_results", "reliability")
