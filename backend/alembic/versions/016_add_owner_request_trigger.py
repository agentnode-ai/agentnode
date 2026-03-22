"""Add owner_request to verification_trigger enum.

Revision ID: 016
Revises: 015
"""
from alembic import op


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE verification_trigger ADD VALUE IF NOT EXISTS 'owner_request'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; no-op.
    pass
