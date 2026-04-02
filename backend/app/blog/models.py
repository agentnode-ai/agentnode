from sqlalchemy import Boolean, Column, Enum, ForeignKey, Integer, Numeric, Text, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import relationship

from app.shared.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BlogPostType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "blog_post_types"

    name = Column(VARCHAR(100), nullable=False)
    slug = Column(VARCHAR(100), nullable=False, unique=True)
    url_prefix = Column(VARCHAR(100), nullable=False, unique=True)
    description = Column(VARCHAR(300), nullable=True)
    icon = Column(VARCHAR(50), nullable=False, default="article")
    has_archive = Column(Boolean, nullable=False, default=True)
    is_system = Column(Boolean, nullable=False, default=False)
    archive_title = Column(VARCHAR(200), nullable=True)
    archive_description = Column(VARCHAR(500), nullable=True)
    sitemap_priority = Column(Numeric(2, 1), nullable=False, default=0.6)
    sitemap_changefreq = Column(VARCHAR(20), nullable=False, default="weekly")
    sort_order = Column(Integer, nullable=False, default=0)

    posts = relationship("BlogPost", back_populates="post_type")


class BlogCategory(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "blog_categories"

    name = Column(VARCHAR(100), nullable=False)
    slug = Column(VARCHAR(100), nullable=False, unique=True)
    description = Column(VARCHAR(300), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")

    posts = relationship("BlogPost", back_populates="category")


class BlogPost(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "blog_posts"

    title = Column(VARCHAR(255), nullable=False)
    slug = Column(VARCHAR(255), nullable=False, unique=True, index=True)
    content_json = Column(JSONB, nullable=True)
    content_html = Column(Text, nullable=True)
    excerpt = Column(VARCHAR(500), nullable=True)
    cover_image_url = Column(VARCHAR(500), nullable=True)
    cover_image_alt = Column(VARCHAR(255), nullable=True)
    seo_title = Column(VARCHAR(70), nullable=True)
    seo_description = Column(VARCHAR(170), nullable=True)
    og_image_url = Column(VARCHAR(500), nullable=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("blog_categories.id", ondelete="SET NULL"), nullable=True, index=True)
    tags = Column(JSONB, default=list)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(
        Enum("draft", "published", "archived", name="blog_post_status", create_type=False),
        nullable=False,
        default="draft",
    )
    published_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reading_time_min = Column(Integer, nullable=True)
    is_featured = Column(Boolean, nullable=False, default=False)
    post_type_id = Column(UUID(as_uuid=True), ForeignKey("blog_post_types.id", ondelete="RESTRICT"), nullable=False, index=True)
    previous_url = Column(VARCHAR(500), nullable=True)

    category = relationship("BlogCategory", back_populates="posts")
    author = relationship("User")
    images = relationship("BlogImage", back_populates="post")
    post_type = relationship("BlogPostType", back_populates="posts")


class BlogImage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "blog_images"

    post_id = Column(UUID(as_uuid=True), ForeignKey("blog_posts.id", ondelete="SET NULL"), nullable=True, index=True)
    object_key = Column(VARCHAR(500), nullable=False)
    url = Column(VARCHAR(500), nullable=False)
    alt_text = Column(VARCHAR(255), nullable=True)
    file_size = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    title = Column(VARCHAR(255), nullable=True)
    original_filename = Column(VARCHAR(255), nullable=True)
    caption = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")

    post = relationship("BlogPost", back_populates="images")
