"""Enable pg_trgm extension and add GIN trigram index on packages.slug.

Covers audit items:
- DB C6: pg_trgm for typosquatting detection
- Perf 3.4: Eliminate full-table scan in get_all_package_slugs
- Perf 4.2: Use database-level fuzzy matching

Revision ID: 023
Revises: 022
"""
from alembic import op
from sqlalchemy import text


revision = "023"
down_revision = "022"


def _index_exists(conn, index_name: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": index_name})
    return result.scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # Enable pg_trgm extension (idempotent)
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # GIN trigram index on packages.slug for similarity queries
    if not _index_exists(conn, "idx_packages_slug_trgm"):
        op.execute(
            "CREATE INDEX idx_packages_slug_trgm ON packages USING gin (slug gin_trgm_ops)"
        )


def downgrade() -> None:
    op.drop_index("idx_packages_slug_trgm", "packages")
    # Note: not dropping pg_trgm extension as other things may depend on it
