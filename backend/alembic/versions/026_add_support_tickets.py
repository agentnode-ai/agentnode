"""Add support_tickets and support_messages tables.

Revision ID: 026
Revises: 025
"""
from alembic import op
from sqlalchemy import text

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = :table"
    ), {"table": table})
    return result.scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # Create sequence for ticket numbers
    conn.execute(text(
        "CREATE SEQUENCE IF NOT EXISTS support_ticket_number_seq START 1"
    ))

    if not _table_exists(conn, "support_tickets"):
        op.create_table(
            "support_tickets",
            __import__("sqlalchemy").Column("id", __import__("sqlalchemy").dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=__import__("sqlalchemy").text("gen_random_uuid()")),
            __import__("sqlalchemy").Column("ticket_number", __import__("sqlalchemy").Integer(), nullable=False, unique=True, server_default=__import__("sqlalchemy").text("nextval('support_ticket_number_seq')")),
            __import__("sqlalchemy").Column("user_id", __import__("sqlalchemy").dialects.postgresql.UUID(as_uuid=True), __import__("sqlalchemy").ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            __import__("sqlalchemy").Column("category", __import__("sqlalchemy").String(50), nullable=False),
            __import__("sqlalchemy").Column("subject", __import__("sqlalchemy").String(200), nullable=False),
            __import__("sqlalchemy").Column("status", __import__("sqlalchemy").String(20), nullable=False, server_default="open"),
            __import__("sqlalchemy").Column("created_at", __import__("sqlalchemy").TIMESTAMP(timezone=True), server_default=__import__("sqlalchemy").text("now()")),
            __import__("sqlalchemy").Column("updated_at", __import__("sqlalchemy").TIMESTAMP(timezone=True), server_default=__import__("sqlalchemy").text("now()")),
            __import__("sqlalchemy").Column("resolved_at", __import__("sqlalchemy").TIMESTAMP(timezone=True), nullable=True),
        )

    if not _table_exists(conn, "support_messages"):
        op.create_table(
            "support_messages",
            __import__("sqlalchemy").Column("id", __import__("sqlalchemy").dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=__import__("sqlalchemy").text("gen_random_uuid()")),
            __import__("sqlalchemy").Column("ticket_id", __import__("sqlalchemy").dialects.postgresql.UUID(as_uuid=True), __import__("sqlalchemy").ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True),
            __import__("sqlalchemy").Column("user_id", __import__("sqlalchemy").dialects.postgresql.UUID(as_uuid=True), __import__("sqlalchemy").ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            __import__("sqlalchemy").Column("is_admin", __import__("sqlalchemy").Boolean(), nullable=False, server_default="false"),
            __import__("sqlalchemy").Column("body", __import__("sqlalchemy").Text(), nullable=False),
            __import__("sqlalchemy").Column("created_at", __import__("sqlalchemy").TIMESTAMP(timezone=True), server_default=__import__("sqlalchemy").text("now()")),
        )


def downgrade() -> None:
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
    op.get_bind().execute(text("DROP SEQUENCE IF EXISTS support_ticket_number_seq"))
