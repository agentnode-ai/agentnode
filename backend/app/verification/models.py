from sqlalchemy import (
    Boolean, Column, Enum, Float, ForeignKey, Integer, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

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

    # Step-level timing (ms)
    install_duration_ms = Column(Integer, nullable=True)
    import_duration_ms = Column(Integer, nullable=True)
    smoke_duration_ms = Column(Integer, nullable=True)
    tests_duration_ms = Column(Integer, nullable=True)

    error_summary = Column(Text, nullable=True)
    warnings_count = Column(Integer, nullable=False, default=0, server_default="0")
    warnings_summary = Column(Text, nullable=True)

    # Phase 3A: Dominant smoke reason for querying/filtering
    smoke_reason = Column(Text, nullable=True, index=True)

    # Phase 4A: Score engine fields
    reliability = Column(Float, nullable=True)
    determinism_score = Column(Float, nullable=True)
    contract_valid = Column(Boolean, nullable=True)
    stability_log = Column(JSONB, nullable=True)
    verification_score = Column(Integer, nullable=True)
    verification_tier = Column(Text, nullable=True)
    score_breakdown = Column(JSONB, nullable=True)
    tests_auto_generated = Column(Boolean, nullable=True)

    # Phase 5B: Environment info (capabilities, python version, sandbox mode)
    environment_info = Column(JSONB, nullable=True)

    # Phase 6E: Verification mode (real/mock/limited)
    verification_mode = Column(Text, nullable=True)

    # Phase 6A: Contract validation details
    contract_details = Column(JSONB, nullable=True)

    # Phase 6B: Confidence level (high/medium/low)
    confidence = Column(Text, nullable=True)

    # Phase 7A: Smoke confidence (high/medium for credential boundary)
    smoke_confidence = Column(Text, nullable=True)

    # Runner metadata for audit/forensics
    runner_version = Column(Text, nullable=True)
    python_version = Column(Text, nullable=True)
    runner_platform = Column(Text, nullable=True)

    # Trigger tracking for re-verify history (typed enum, not free string)
    triggered_by = Column(
        Enum("publish", "admin_reverify", "runner_upgrade", "scheduled", "owner_request", name="verification_trigger", create_type=False),
        nullable=True,
    )

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
