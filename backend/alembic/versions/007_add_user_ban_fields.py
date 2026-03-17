"""Add is_banned and ban_reason fields to users table.

Revision ID: 007
Revises: 006
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_banned", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("ban_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "ban_reason")
    op.drop_column("users", "is_banned")
