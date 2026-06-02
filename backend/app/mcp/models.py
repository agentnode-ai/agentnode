"""MCP submission models."""
from sqlalchemy import Column, ForeignKey, Text, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.shared.models import Base, UUIDPrimaryKeyMixin, TimestampMixin


class McpSubmission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "mcp_submissions"

    publisher_id = Column(UUID(as_uuid=True), ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False)
    package_name = Column(VARCHAR(200), nullable=False)
    package_registry = Column(VARCHAR(20), nullable=False)
    package_version = Column(VARCHAR(50))
    source_repo = Column(Text)
    manifest_raw = Column(JSONB, nullable=False)
    verification_report = Column(JSONB, nullable=False)  # client report, advisory
    server_verification = Column(JSONB)  # backend-derived registry facts, authoritative
    status = Column(VARCHAR(40), nullable=False, default="pending")
    reviewer_notes = Column(Text)
    maintainer_feedback = Column(Text)
    reviewed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = Column(TIMESTAMP(timezone=True))
    published_package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id", ondelete="SET NULL"))


class PublisherPackageClaim(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Proof that a publisher controls a specific npm/PyPI package — the
    `package_control` axis. Kept STRICTLY separate from server_verification's
    `repo_consistency` (which only proves manifest/registry agree on a repo).
    Step 1: method=manual_admin only; challenge fields are prepared but unused.
    """
    __tablename__ = "publisher_package_claims"

    publisher_id = Column(UUID(as_uuid=True), ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False)
    registry = Column(VARCHAR(20), nullable=False)            # npm | pypi
    package_name = Column(VARCHAR(200), nullable=False)       # as submitted
    package_name_normalized = Column(VARCHAR(200), nullable=False)  # match key
    method = Column(VARCHAR(30), nullable=False)              # manual_admin (registry_challenge later)
    strength = Column(VARCHAR(20), nullable=False)            # manual (strong later)
    status = Column(VARCHAR(20), nullable=False, default="verified")  # verified | rejected | expired
    evidence = Column(JSONB)
    challenge_token_hash = Column(VARCHAR(128))               # prepared for registry_challenge, unused in Step 1
    verified_at = Column(TIMESTAMP(timezone=True))
    verified_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    expires_at = Column(TIMESTAMP(timezone=True))
