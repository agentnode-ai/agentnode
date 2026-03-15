from sqlalchemy import Boolean, Column, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.shared.models import Base, UUIDPrimaryKeyMixin


class Webhook(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "webhooks"

    publisher_id = Column(UUID(as_uuid=True), ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(Text, nullable=False)
    secret = Column(Text, nullable=True)
    events = Column(JSONB, nullable=False, default=[])
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")


class WebhookDelivery(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "webhook_deliveries"

    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=False)
    status_code = Column(Text, nullable=True)
    success = Column(Boolean, nullable=False, default=False)
    delivered_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
