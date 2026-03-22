from sqlalchemy import Boolean, Column, Integer, Numeric, VARCHAR
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from app.shared.models import Base, UUIDPrimaryKeyMixin


class SitemapPage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "sitemap_pages"

    path = Column(VARCHAR(300), nullable=False, unique=True)
    priority = Column(Numeric(2, 1), nullable=False, default=0.5)
    changefreq = Column(VARCHAR(20), nullable=False, default="monthly")
    indexable = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
