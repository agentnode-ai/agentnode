"""Add install_count column to packages and backfill from installations table.

Separates download tracking (anonymous artifact fetches) from install tracking
(authenticated SDK/CLI installations).

Revision ID: 025
Revises: 024
"""
from alembic import op
from sqlalchemy import text

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :table AND column_name = :col"
    ), {"table": table, "col": column})
    return result.scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, "packages", "install_count"):
        op.add_column(
            "packages",
            __import__("sqlalchemy").Column(
                "install_count",
                __import__("sqlalchemy").Integer(),
                nullable=False,
                server_default="0",
            ),
        )

    # Backfill install_count from installations table (whitelist: installed, active)
    conn.execute(text("""
        UPDATE packages SET install_count = sub.cnt
        FROM (
            SELECT package_id, COUNT(*) AS cnt
            FROM installations
            WHERE status IN ('installed', 'active')
            GROUP BY package_id
        ) AS sub
        WHERE packages.id = sub.package_id
    """))


def downgrade() -> None:
    op.drop_column("packages", "install_count")
