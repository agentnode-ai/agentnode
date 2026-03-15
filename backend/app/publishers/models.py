from sqlalchemy import Boolean, Column, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.shared.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Publisher(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "publishers"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    display_name = Column(Text, nullable=False)
    slug = Column(Text, nullable=False, unique=True, index=True)
    bio = Column(Text, nullable=True)
    website_url = Column(Text, nullable=True)
    github_url = Column(Text, nullable=True)
    trust_level = Column(
        Enum("unverified", "verified", "trusted", "curated", name="trust_level", create_type=False),
        nullable=False,
        default="unverified",
    )
    is_suspended = Column(Boolean, nullable=False, default=False)
    suspension_reason = Column(Text, nullable=True)
    packages_published_count = Column(Integer, nullable=False, default=0)
    packages_cleared_count = Column(Integer, nullable=False, default=0)

    user = relationship("User", back_populates="publisher")
