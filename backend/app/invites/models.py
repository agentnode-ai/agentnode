from sqlalchemy import Column, ForeignKey, Integer, Text, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.shared.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ImportCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "import_candidates"

    source = Column(VARCHAR(50), nullable=False)
    source_url = Column(Text, nullable=False)
    repo_owner = Column(VARCHAR(100))
    repo_name = Column(VARCHAR(100))
    display_name = Column(Text)
    description = Column(Text)
    detected_tools = Column(JSONB)
    detected_format = Column(VARCHAR(20))
    license_spdx = Column(Text)
    stars = Column(Integer)

    contact_email = Column(Text)
    contact_name = Column(Text)
    contact_channel = Column(VARCHAR(20))
    assigned_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    outreach_status = Column(VARCHAR(20), nullable=False, default="discovered")
    contacted_at = Column(TIMESTAMP(timezone=True))
    published_package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id", ondelete="SET NULL"))

    last_event_at = Column(TIMESTAMP(timezone=True))
    last_event_type = Column(VARCHAR(50))

    admin_notes = Column(Text)
    skip_reason = Column(Text)


class InviteCode(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "invite_codes"

    code = Column(VARCHAR(40), unique=True, nullable=False)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("import_candidates.id", ondelete="SET NULL"))
    prefill_data = Column(JSONB)
    claimed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    status = Column(VARCHAR(20), nullable=False, default="active")
    expires_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)


class CandidateEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "candidate_events"

    candidate_id = Column(UUID(as_uuid=True), ForeignKey("import_candidates.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(VARCHAR(50), nullable=False)
    metadata_ = Column("metadata", JSONB)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
