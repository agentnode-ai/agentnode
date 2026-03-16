"""Add signing_public_key to publishers for Ed25519 signature verification.

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publishers", sa.Column("signing_public_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("publishers", "signing_public_key")
