"""Add per-tool entrypoint column to capabilities table for ANP v0.2.

Revision ID: 006
Revises: 005
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("capabilities", sa.Column("entrypoint", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("capabilities", "entrypoint")
