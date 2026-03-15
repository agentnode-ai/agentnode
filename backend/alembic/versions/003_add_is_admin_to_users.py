"""Add is_admin column to users table.

Revision ID: 003
Revises: 002
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN is_admin")
