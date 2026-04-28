"""Add agent-specific verification fields to verification_results.

Enables bifurcated scoring for agents vs tool-packs:
- is_agent_package: dispatch flag for scoring
- manifest_completeness: agent manifest quality score
- agent_cases_*: verification case results
- agent_gold_blockers: reasons why Gold was not achieved

Revision ID: 030
Revises: 029
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "030"
down_revision = "029"


def upgrade():
    op.add_column("verification_results", sa.Column("is_agent_package", sa.Boolean(), nullable=True))
    op.add_column("verification_results", sa.Column("manifest_completeness", JSONB(), nullable=True))
    op.add_column("verification_results", sa.Column("agent_cases_results", JSONB(), nullable=True))
    op.add_column("verification_results", sa.Column("agent_cases_passed", sa.Integer(), nullable=True))
    op.add_column("verification_results", sa.Column("agent_cases_total", sa.Integer(), nullable=True))
    op.add_column("verification_results", sa.Column("agent_gold_blockers", JSONB(), nullable=True))


def downgrade():
    op.drop_column("verification_results", "agent_gold_blockers")
    op.drop_column("verification_results", "agent_cases_total")
    op.drop_column("verification_results", "agent_cases_passed")
    op.drop_column("verification_results", "agent_cases_results")
    op.drop_column("verification_results", "manifest_completeness")
    op.drop_column("verification_results", "is_agent_package")
