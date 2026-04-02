"""Create missing tables (installations, reviews, package_reports, admin_audit_logs,
system_settings), missing enums, missing columns (users.email_preferences), and
add UniqueConstraint on import_candidates(source, source_url) if absent.

Covers audit items:
- DB C1: Missing tables that exist in models but were never migrated
- DB H7: (fixed in env.py) — all model modules now imported for autogenerate
- DB H8: UniqueConstraint on import_candidates(source, source_url)

Revision ID: 022
Revises: 021
"""
from alembic import op
from sqlalchemy import text


revision = "022"
down_revision = "021"


# ── helpers ──────────────────────────────────────────────────────────────

def _table_exists(conn, name: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = :name"
    ), {"name": name})
    return result.scalar() is not None


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :table AND column_name = :col"
    ), {"table": table, "col": column})
    return result.scalar() is not None


def _enum_exists(conn, name: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM pg_type WHERE typname = :name"
    ), {"name": name})
    return result.scalar() is not None


def _constraint_exists(conn, name: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE constraint_name = :name"
    ), {"name": name})
    return result.scalar() is not None


def _index_exists(conn, name: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": name})
    return result.scalar() is not None


# ── upgrade ──────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Missing enums ─────────────────────────────────────────────────

    if not _enum_exists(conn, "install_source"):
        conn.execute(text(
            "CREATE TYPE install_source AS ENUM "
            "('cli', 'api', 'web', 'sdk', 'adapter')"
        ))

    if not _enum_exists(conn, "install_status"):
        conn.execute(text(
            "CREATE TYPE install_status AS ENUM "
            "('installed', 'active', 'failed', 'uninstalled')"
        ))

    if not _enum_exists(conn, "install_event_type"):
        conn.execute(text(
            "CREATE TYPE install_event_type AS ENUM "
            "('install', 'update', 'rollback')"
        ))

    # ── 2. Missing tables ────────────────────────────────────────────────

    # 2a. installations
    if not _table_exists(conn, "installations"):
        conn.execute(text("""
            CREATE TABLE installations (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id             UUID REFERENCES users(id) ON DELETE SET NULL,
                package_id          UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
                package_version_id  UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
                source              install_source NOT NULL,
                status              install_status NOT NULL DEFAULT 'installed',
                event_type          install_event_type NOT NULL DEFAULT 'install',
                installation_context JSONB DEFAULT '{}'::jsonb,
                installed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                activated_at        TIMESTAMPTZ,
                uninstalled_at      TIMESTAMPTZ
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_installations_user_id ON installations(user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_installations_package_id ON installations(package_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_installations_package_version_id ON installations(package_version_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_installations_status ON installations(status)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_installations_installed_at ON installations(installed_at)"
        ))

    # 2b. reviews
    if not _table_exists(conn, "reviews"):
        conn.execute(text("""
            CREATE TABLE reviews (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                package_id  UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
                rating      INTEGER NOT NULL,
                comment     TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(user_id, package_id)
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_reviews_package_id ON reviews(package_id)"
        ))

    # 2c. package_reports
    if not _table_exists(conn, "package_reports"):
        conn.execute(text("""
            CREATE TABLE package_reports (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                package_id        UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
                reporter_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
                reason            TEXT NOT NULL,
                description       TEXT,
                status            TEXT NOT NULL DEFAULT 'submitted',
                resolved_by       UUID REFERENCES users(id),
                resolution_note   TEXT,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                resolved_at       TIMESTAMPTZ
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_package_reports_status ON package_reports(status)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_package_reports_reporter_user_id ON package_reports(reporter_user_id)"
        ))

    # 2d. admin_audit_logs
    if not _table_exists(conn, "admin_audit_logs"):
        conn.execute(text("""
            CREATE TABLE admin_audit_logs (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                admin_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
                action          VARCHAR(100) NOT NULL,
                target_type     VARCHAR(50) NOT NULL,
                target_id       VARCHAR(255) NOT NULL,
                metadata        JSONB DEFAULT '{}'::jsonb,
                ip_address      VARCHAR(45),
                user_agent      TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_admin_audit_logs_admin_user_id ON admin_audit_logs(admin_user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_admin_audit_logs_action ON admin_audit_logs(action)"
        ))

    # 2e. system_settings
    if not _table_exists(conn, "system_settings"):
        conn.execute(text("""
            CREATE TABLE system_settings (
                key         VARCHAR(100) PRIMARY KEY,
                value       JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

    # ── 3. Missing columns ───────────────────────────────────────────────

    # 3a. users.email_preferences (JSONB, server_default='{}')
    if not _column_exists(conn, "users", "email_preferences"):
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN email_preferences JSONB NOT NULL DEFAULT '{}'::jsonb"
        ))

    # ── 4. DB H8: UniqueConstraint on import_candidates(source, source_url) ──
    # Migration 018 includes UNIQUE(source, source_url) in the CREATE TABLE,
    # but if the constraint was dropped or never applied, re-add it idempotently.
    if _table_exists(conn, "import_candidates"):
        # Check for any unique constraint on (source, source_url)
        result = conn.execute(text("""
            SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            WHERE t.relname = 'import_candidates'
              AND c.contype = 'u'
              AND array_length(c.conkey, 1) = 2
        """))
        if result.scalar() is None:
            conn.execute(text(
                "ALTER TABLE import_candidates "
                "ADD CONSTRAINT uq_import_candidates_source_url "
                "UNIQUE (source, source_url)"
            ))


# ── downgrade ────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    # Remove the unique constraint we may have added
    conn.execute(text(
        "ALTER TABLE import_candidates "
        "DROP CONSTRAINT IF EXISTS uq_import_candidates_source_url"
    ))

    # Remove added column
    conn.execute(text(
        "ALTER TABLE users DROP COLUMN IF EXISTS email_preferences"
    ))

    # Drop tables in dependency order
    conn.execute(text("DROP TABLE IF EXISTS system_settings"))
    conn.execute(text("DROP TABLE IF EXISTS admin_audit_logs"))
    conn.execute(text("DROP TABLE IF EXISTS package_reports"))
    conn.execute(text("DROP TABLE IF EXISTS reviews"))
    conn.execute(text("DROP TABLE IF EXISTS installations"))

    # Drop enums
    conn.execute(text("DROP TYPE IF EXISTS install_event_type"))
    conn.execute(text("DROP TYPE IF EXISTS install_status"))
    conn.execute(text("DROP TYPE IF EXISTS install_source"))
