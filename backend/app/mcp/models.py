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
    verification_report = Column(JSONB, nullable=False)
    status = Column(VARCHAR(40), nullable=False, default="pending")
    reviewer_notes = Column(Text)
    maintainer_feedback = Column(Text)
    reviewed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = Column(TIMESTAMP(timezone=True))
    published_package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id", ondelete="SET NULL"))
