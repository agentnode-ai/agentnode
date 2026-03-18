from sqlalchemy import (
    Column, Enum, ForeignKey, Integer, Text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from app.shared.models import Base, UUIDPrimaryKeyMixin

# Step-level status enum (shared across all 4 steps)
_step_status_enum = Enum(
    "passed", "failed", "skipped", "error", "not_present", "inconclusive",
    name="step_status",
    create_type=False,
)


class VerificationResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "verification_results"

    package_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("package_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        # NOT unique — multiple runs per version (re-verify, improved runners)
    )
    status = Column(
        Enum(
            "pending", "running", "passed", "failed", "error", "skipped",
            name="verification_status",
            create_type=False,
        ),
        nullable=False,
        default="pending",
    )
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Step statuses: passed/failed/skipped/error/not_present/inconclusive
    install_status = Column(_step_status_enum, nullable=True)
    import_status = Column(_step_status_enum, nullable=True)
    smoke_status = Column(_step_status_enum, nullable=True)
    tests_status = Column(_step_status_enum, nullable=True)

    install_log = Column(Text, nullable=True)
    import_log = Column(Text, nullable=True)
    smoke_log = Column(Text, nullable=True)
    tests_log = Column(Text, nullable=True)

    error_summary = Column(Text, nullable=True)
    warnings_count = Column(Integer, nullable=False, default=0, server_default="0")
    warnings_summary = Column(Text, nullable=True)

    # Runner metadata for audit/forensics
    runner_version = Column(Text, nullable=True)
    python_version = Column(Text, nullable=True)
    runner_platform = Column(Text, nullable=True)

    # Trigger tracking for re-verify history (typed enum, not free string)
    triggered_by = Column(
        Enum("publish", "admin_reverify", "runner_upgrade", name="verification_trigger", create_type=False),
        nullable=True,
    )

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
