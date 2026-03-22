"""Add package enrichment fields to package_versions and verification_results.

Revision ID: 012
Revises: 011
"""
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New columns on package_versions
    op.execute("ALTER TABLE package_versions ADD COLUMN readme_md TEXT")
    op.execute("ALTER TABLE package_versions ADD COLUMN file_list JSONB")
    op.execute("ALTER TABLE package_versions ADD COLUMN env_requirements JSONB")
    op.execute("ALTER TABLE package_versions ADD COLUMN use_cases JSONB")
    op.execute("ALTER TABLE package_versions ADD COLUMN examples JSONB")
    op.execute("ALTER TABLE package_versions ADD COLUMN homepage_url TEXT")
    op.execute("ALTER TABLE package_versions ADD COLUMN docs_url TEXT")
    op.execute("ALTER TABLE package_versions ADD COLUMN source_url TEXT")

    # Step-level timing on verification_results
    op.execute("ALTER TABLE verification_results ADD COLUMN install_duration_ms INTEGER")
    op.execute("ALTER TABLE verification_results ADD COLUMN import_duration_ms INTEGER")
    op.execute("ALTER TABLE verification_results ADD COLUMN smoke_duration_ms INTEGER")
    op.execute("ALTER TABLE verification_results ADD COLUMN tests_duration_ms INTEGER")

    # Usage metrics (internal, not displayed yet)
    op.execute("ALTER TABLE package_versions ADD COLUMN execution_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE package_versions ADD COLUMN execution_success_count INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS execution_success_count")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS execution_count")
    op.execute("ALTER TABLE verification_results DROP COLUMN IF EXISTS tests_duration_ms")
    op.execute("ALTER TABLE verification_results DROP COLUMN IF EXISTS smoke_duration_ms")
    op.execute("ALTER TABLE verification_results DROP COLUMN IF EXISTS import_duration_ms")
    op.execute("ALTER TABLE verification_results DROP COLUMN IF EXISTS install_duration_ms")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS source_url")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS docs_url")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS homepage_url")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS examples")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS use_cases")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS env_requirements")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS file_list")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS readme_md")
