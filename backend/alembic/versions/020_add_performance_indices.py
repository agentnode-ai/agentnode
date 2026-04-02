"""Add performance indices and FK constraints.

Covers audit items:
- DB H1-H6: Missing indices on Installation, PackageReport, Review, SecurityFinding, Dependency
- Perf 2.2: Index on verification_status
- Perf 2.3: Composite version listing index

Note: quarantine_status index (Perf 2.1) already exists as idx_versions_quarantine.

Revision ID: 020
Revises: 019
"""
from alembic import op
from sqlalchemy import text


revision = "020"
down_revision = "019"


def _index_exists(conn, index_name: str) -> bool:
    result = conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": index_name})
    return result.scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()

    indices = [
        # Installation indices (DB H1, H2)
        ("ix_installations_status", "installations", ["status"]),
        ("ix_installations_package_version_id", "installations", ["package_version_id"]),
        ("ix_installations_installed_at", "installations", ["installed_at"]),
        # PackageReport indices (DB H3)
        ("ix_package_reports_status", "package_reports", ["status"]),
        ("ix_package_reports_reporter_user_id", "package_reports", ["reporter_user_id"]),
        # Review index (DB H4)
        ("ix_reviews_package_id", "reviews", ["package_id"]),
        # SecurityFinding index (DB H5)
        ("ix_security_findings_package_version_id", "security_findings", ["package_version_id"]),
        # Dependency index (DB H6)
        ("ix_dependencies_package_version_id", "dependencies", ["package_version_id"]),
        # PackageVersion verification_status (Perf 2.2)
        ("ix_package_versions_verification_status", "package_versions", ["verification_status"]),
    ]

    for name, table, columns in indices:
        if not _index_exists(conn, name):
            op.create_index(name, table, columns)

    # Composite listing index (Perf 2.3)
    if not _index_exists(conn, "ix_package_versions_listing"):
        op.create_index(
            "ix_package_versions_listing",
            "package_versions",
            ["package_id", "quarantine_status", "is_yanked", "channel", "published_at"],
        )


def downgrade() -> None:
    for name, table in [
        ("ix_package_versions_listing", "package_versions"),
        ("ix_package_versions_verification_status", "package_versions"),
        ("ix_dependencies_package_version_id", "dependencies"),
        ("ix_security_findings_package_version_id", "security_findings"),
        ("ix_reviews_package_id", "reviews"),
        ("ix_package_reports_reporter_user_id", "package_reports"),
        ("ix_package_reports_status", "package_reports"),
        ("ix_installations_installed_at", "installations"),
        ("ix_installations_package_version_id", "installations"),
        ("ix_installations_status", "installations"),
    ]:
        op.drop_index(name, table)
