from sqlalchemy import Column, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID, TIMESTAMP

from app.shared.models import Base, UUIDPrimaryKeyMixin


class AdminAuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "admin_audit_logs"

    admin_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)
    target_type = Column(String(50), nullable=False)  # user, publisher, package, report, capability
    target_id = Column(String(255), nullable=False)
    metadata_ = Column("metadata", JSONB, default={})
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSONB, nullable=False, default={})
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
