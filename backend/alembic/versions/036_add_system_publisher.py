"""Add is_system_publisher to publishers + seed agentnode-community publisher.

Supports MCP Discovery (M1): system publisher for community-seeded packages.
is_system_publisher is internal-only, never client-settable.

Revision ID: 036
Revises: 035
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision = "036"
down_revision = "035"


def upgrade() -> None:
    op.add_column(
        "publishers",
        sa.Column("is_system_publisher", sa.Boolean(), nullable=False, server_default="false"),
    )

    conn = op.get_bind()

    # Create system user if not exists
    conn.execute(text("""
        INSERT INTO users (id, email, username, password_hash)
        VALUES (gen_random_uuid(), 'system@agentnode.net', 'agentnode-system', '!locked')
        ON CONFLICT (email) DO NOTHING
    """))

    # Get system user id
    result = conn.execute(text(
        "SELECT id FROM users WHERE email = 'system@agentnode.net'"
    ))
    row = result.fetchone()
    if row is None:
        return

    user_id = row[0]

    # Create system publisher if not exists
    conn.execute(text("""
        INSERT INTO publishers (id, user_id, display_name, slug, trust_level, is_system_publisher)
        VALUES (gen_random_uuid(), :user_id, 'AgentNode Community', 'agentnode-community', 'unverified', true)
        ON CONFLICT (slug) DO NOTHING
    """), {"user_id": user_id})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "DELETE FROM publishers WHERE slug = 'agentnode-community'"
    ))
    conn.execute(text(
        "DELETE FROM users WHERE email = 'system@agentnode.net'"
    ))
    op.drop_column("publishers", "is_system_publisher")
