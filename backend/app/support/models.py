from sqlalchemy import TIMESTAMP, Boolean, Column, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.shared.models import Base, UUIDPrimaryKeyMixin


class SupportTicket(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "support_tickets"

    ticket_number = Column(Integer, unique=True, nullable=False, server_default=text("nextval('support_ticket_number_seq')"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(50), nullable=False)
    subject = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False, default="open")
    created_at = Column(TIMESTAMP(timezone=True), server_default="now()")
    updated_at = Column(TIMESTAMP(timezone=True), server_default="now()")
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)


class SupportMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "support_messages"

    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    body = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default="now()")
