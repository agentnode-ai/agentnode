"""Add verification_results table, step_status enum, and verification fields on package_versions.

Revision ID: 008
Revises: 007

Uses raw SQL to avoid SQLAlchemy's Enum auto-creation bug with asyncpg
(DuplicateObjectError when create_type=False is ignored during create_table).
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums
    op.execute("CREATE TYPE verification_status AS ENUM ('pending', 'running', 'passed', 'failed', 'error', 'skipped')")
    op.execute("CREATE TYPE step_status AS ENUM ('passed', 'failed', 'skipped', 'error', 'not_present', 'inconclusive')")
    op.execute("CREATE TYPE verification_trigger AS ENUM ('publish', 'admin_reverify', 'runner_upgrade')")

    # Create verification_results table
    op.execute("""
        CREATE TABLE verification_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            status verification_status NOT NULL DEFAULT 'pending',
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            duration_ms INTEGER,
            install_status step_status,
            import_status step_status,
            smoke_status step_status,
            tests_status step_status,
            install_log TEXT,
            import_log TEXT,
            smoke_log TEXT,
            tests_log TEXT,
            error_summary TEXT,
            warnings_count INTEGER NOT NULL DEFAULT 0,
            warnings_summary TEXT,
            runner_version TEXT,
            python_version TEXT,
            runner_platform TEXT,
            triggered_by verification_trigger,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_verification_results_package_version_id ON verification_results (package_version_id)")

    # Add verification columns to package_versions
    op.execute("ALTER TABLE package_versions ADD COLUMN verification_status verification_status NOT NULL DEFAULT 'pending'")
    op.execute("ALTER TABLE package_versions ADD COLUMN latest_verification_result_id UUID REFERENCES verification_results(id) ON DELETE SET NULL")
    op.execute("ALTER TABLE package_versions ADD COLUMN verification_run_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE package_versions ADD COLUMN last_verified_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS last_verified_at")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS verification_run_count")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS latest_verification_result_id")
    op.execute("ALTER TABLE package_versions DROP COLUMN IF EXISTS verification_status")
    op.execute("DROP TABLE IF EXISTS verification_results")
    op.execute("DROP TYPE IF EXISTS verification_trigger")
    op.execute("DROP TYPE IF EXISTS step_status")
    op.execute("DROP TYPE IF EXISTS verification_status")
