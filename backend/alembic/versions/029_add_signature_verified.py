"""Persist signature verification result on package_versions.

P1-L2: the publish flow computed `signature_verified` but never stored it,
so later code paths (install metadata, admin review) had no way to tell
whether a supplied signature was actually checked against the publisher's
registered key. Default `false` is safe: older rows had no verification
recorded, and nothing in the hot path should treat unverified as verified.

Revision ID: 029
Revises: 028
"""
from alembic import op
import sqlalchemy as sa

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "package_versions",
        sa.Column(
            "signature_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("package_versions", "signature_verified")
