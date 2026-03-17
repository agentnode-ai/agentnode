from sqlalchemy import Boolean, Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import relationship

from app.shared.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email = Column(Text, nullable=False, unique=True, index=True)
    username = Column(Text, nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    is_email_verified = Column(Boolean, nullable=False, default=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    two_factor_secret = Column(Text, nullable=True)
    two_factor_enabled = Column(Boolean, nullable=False, default=False)
    is_banned = Column(Boolean, nullable=False, default=False)
    ban_reason = Column(Text, nullable=True)
    email_preferences = Column(JSONB, nullable=False, server_default='{}')

    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    publisher = relationship("Publisher", back_populates="user", uselist=False)


class ApiKey(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "api_keys"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key_prefix = Column(Text, nullable=False, index=True)
    key_hash_sha256 = Column(Text, nullable=False)
    label = Column(Text, nullable=True)
    last_used_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)

    user = relationship("User", back_populates="api_keys")
