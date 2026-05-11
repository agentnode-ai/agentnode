"""Add 'token' to connector_auth_type enum.

Supports connector packages that declare auth_type: 'token' (e.g. Linear PAT,
Notion integration tokens). Static bearer tokens without OAuth lifecycle.

Revision ID: 034
Revises: 033
"""
from alembic import op
from sqlalchemy import text


revision = "034"
down_revision = "033"


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(text(
        "SELECT 1 FROM pg_enum "
        "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "WHERE pg_type.typname = 'connector_auth_type' "
        "AND pg_enum.enumlabel = 'token'"
    ))
    if result.scalar() is None:
        conn.execute(text(
            "ALTER TYPE connector_auth_type ADD VALUE 'token'"
        ))


def downgrade() -> None:
    pass
