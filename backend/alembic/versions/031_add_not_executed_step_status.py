"""Add 'not_executed' to step_status enum.

Used when untrusted publisher tests are present but not run
due to missing container sandbox isolation.

Revision ID: 031
Revises: 030
"""
from alembic import op


revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'not_executed'")


def downgrade() -> None:
    pass
