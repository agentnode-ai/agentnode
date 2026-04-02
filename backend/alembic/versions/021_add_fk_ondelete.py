"""Add ON DELETE behavior to foreign key constraints.

Covers audit items:
- DB C3: PackageReport FKs — CASCADE for package_id, SET NULL for reporter_user_id
- DB C4: AdminAuditLog FK — SET NULL for admin_user_id
- DB C5: Capability FK — CASCADE for capability_id -> capability_taxonomy

Revision ID: 021
Revises: 020
"""
from alembic import op
from sqlalchemy import text


revision = "021"
down_revision = "020"


def upgrade() -> None:
    conn = op.get_bind()

    # ── DB C3: package_reports.package_id → packages(id) ON DELETE CASCADE ──
    conn.execute(text(
        "ALTER TABLE package_reports "
        "DROP CONSTRAINT IF EXISTS package_reports_package_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE package_reports "
        "ADD CONSTRAINT package_reports_package_id_fkey "
        "FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE"
    ))

    # ── DB C3: package_reports.reporter_user_id → users(id) ON DELETE SET NULL ──
    # Column must be nullable to support SET NULL
    conn.execute(text(
        "ALTER TABLE package_reports "
        "ALTER COLUMN reporter_user_id DROP NOT NULL"
    ))
    conn.execute(text(
        "ALTER TABLE package_reports "
        "DROP CONSTRAINT IF EXISTS package_reports_reporter_user_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE package_reports "
        "ADD CONSTRAINT package_reports_reporter_user_id_fkey "
        "FOREIGN KEY (reporter_user_id) REFERENCES users(id) ON DELETE SET NULL"
    ))

    # ── DB C4: admin_audit_logs.admin_user_id → users(id) ON DELETE SET NULL ──
    # Column must be nullable to support SET NULL
    conn.execute(text(
        "ALTER TABLE admin_audit_logs "
        "ALTER COLUMN admin_user_id DROP NOT NULL"
    ))
    conn.execute(text(
        "ALTER TABLE admin_audit_logs "
        "DROP CONSTRAINT IF EXISTS admin_audit_logs_admin_user_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE admin_audit_logs "
        "ADD CONSTRAINT admin_audit_logs_admin_user_id_fkey "
        "FOREIGN KEY (admin_user_id) REFERENCES users(id) ON DELETE SET NULL"
    ))

    # ── DB C5: capabilities.capability_id → capability_taxonomy(id) ON DELETE CASCADE ──
    conn.execute(text(
        "ALTER TABLE capabilities "
        "DROP CONSTRAINT IF EXISTS capabilities_capability_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE capabilities "
        "ADD CONSTRAINT capabilities_capability_id_fkey "
        "FOREIGN KEY (capability_id) REFERENCES capability_taxonomy(id) ON DELETE CASCADE"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # Revert capabilities.capability_id — remove ON DELETE CASCADE
    conn.execute(text(
        "ALTER TABLE capabilities "
        "DROP CONSTRAINT IF EXISTS capabilities_capability_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE capabilities "
        "ADD CONSTRAINT capabilities_capability_id_fkey "
        "FOREIGN KEY (capability_id) REFERENCES capability_taxonomy(id)"
    ))

    # Revert admin_audit_logs.admin_user_id — remove ON DELETE SET NULL, restore NOT NULL
    conn.execute(text(
        "ALTER TABLE admin_audit_logs "
        "DROP CONSTRAINT IF EXISTS admin_audit_logs_admin_user_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE admin_audit_logs "
        "ADD CONSTRAINT admin_audit_logs_admin_user_id_fkey "
        "FOREIGN KEY (admin_user_id) REFERENCES users(id)"
    ))
    conn.execute(text(
        "ALTER TABLE admin_audit_logs "
        "ALTER COLUMN admin_user_id SET NOT NULL"
    ))

    # Revert package_reports.reporter_user_id — remove ON DELETE SET NULL, restore NOT NULL
    conn.execute(text(
        "ALTER TABLE package_reports "
        "DROP CONSTRAINT IF EXISTS package_reports_reporter_user_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE package_reports "
        "ADD CONSTRAINT package_reports_reporter_user_id_fkey "
        "FOREIGN KEY (reporter_user_id) REFERENCES users(id)"
    ))
    conn.execute(text(
        "ALTER TABLE package_reports "
        "ALTER COLUMN reporter_user_id SET NOT NULL"
    ))

    # Revert package_reports.package_id — remove ON DELETE CASCADE
    conn.execute(text(
        "ALTER TABLE package_reports "
        "DROP CONSTRAINT IF EXISTS package_reports_package_id_fkey"
    ))
    conn.execute(text(
        "ALTER TABLE package_reports "
        "ADD CONSTRAINT package_reports_package_id_fkey "
        "FOREIGN KEY (package_id) REFERENCES packages(id)"
    ))
