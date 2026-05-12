"""Add 'skill' to package_type enum.

Supports prompt+assets packages with no code, no runtime, no execution.
Skills use runtime='none' and install_mode='prompt_only'.

Revision ID: 035
Revises: 034
"""
from alembic import op
from sqlalchemy import text


revision = "035"
down_revision = "034"


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(text(
        "SELECT 1 FROM pg_enum "
        "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "WHERE pg_type.typname = 'package_type' "
        "AND pg_enum.enumlabel = 'skill'"
    ))
    if result.scalar() is None:
        conn.execute(text(
            "ALTER TYPE package_type ADD VALUE 'skill'"
        ))


def downgrade() -> None:
    pass
