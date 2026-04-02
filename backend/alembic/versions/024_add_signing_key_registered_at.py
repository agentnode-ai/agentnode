"""Add signing_key_registered_at to publishers.

Tracks when a publisher registered their Ed25519 signing key.

Revision ID: 024
Revises: 023
"""
from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publishers",
        sa.Column("signing_key_registered_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publishers", "signing_key_registered_at")
