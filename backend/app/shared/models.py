import uuid
from datetime import datetime, timezone

from sqlalchemy import TIMESTAMP, Column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UUIDPrimaryKeyMixin:
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
