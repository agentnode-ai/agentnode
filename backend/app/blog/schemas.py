from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


# --- Post Types ---

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class PostTypeCreate(BaseModel):
    name: str = Field(max_length=100)
    slug: str = Field(max_length=100, pattern=SLUG_PATTERN)
    url_prefix: str = Field(max_length=100, pattern=SLUG_PATTERN)
    description: str | None = Field(None, max_length=300)
    icon: str = Field("article", max_length=50)
    has_archive: bool = True
    archive_title: str | None = Field(None, max_length=200)
    archive_description: str | None = Field(None, max_length=500)
    sitemap_priority: Decimal = Decimal("0.6")
    sitemap_changefreq: str = "weekly"
    sort_order: int = 0


class PostTypeUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    slug: str | None = Field(None, max_length=100, pattern=SLUG_PATTERN)
    url_prefix: str | None = Field(None, max_length=100, pattern=SLUG_PATTERN)
    description: str | None = Field(None, max_length=300)
    icon: str | None = Field(None, max_length=50)
    has_archive: bool | None = None
    archive_title: str | None = Field(None, max_length=200)
    archive_description: str | None = Field(None, max_length=500)
    sitemap_priority: Decimal | None = None
    sitemap_changefreq: str | None = None
    sort_order: int | None = None


class PostTypeResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    url_prefix: str
    description: str | None
    icon: str
    has_archive: bool
    is_system: bool
    archive_title: str | None
    archive_description: str | None
    sitemap_priority: Decimal
    sitemap_changefreq: str
    sort_order: int
    post_count: int = 0
    created_at: datetime
    updated_at: datetime


class PostTypeInfo(BaseModel):
    id: UUID
    name: str
    slug: str
    url_prefix: str


# --- Categories ---

class CategoryCreate(BaseModel):
    name: str = Field(max_length=100)
    slug: str = Field(max_length=100, pattern=SLUG_PATTERN)
    description: str | None = Field(None, max_length=300)
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    slug: str | None = Field(None, max_length=100, pattern=SLUG_PATTERN)
    description: str | None = Field(None, max_length=300)
    sort_order: int | None = None


class CategoryResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    sort_order: int
    post_count: int = 0


# --- Posts ---

class PostCreate(BaseModel):
    title: str = Field(max_length=255)
    slug: str = Field(max_length=255, pattern=SLUG_PATTERN)
    content_json: dict | None = None
    content_html: str | None = None
    excerpt: str | None = Field(None, max_length=500)
    cover_image_url: str | None = None
    cover_image_alt: str | None = Field(None, max_length=255)
    seo_title: str | None = Field(None, max_length=70)
    seo_description: str | None = Field(None, max_length=170)
    og_image_url: str | None = None
    category_id: UUID | None = None
    tags: list[str] = []
    is_featured: bool = False
    post_type_id: UUID | None = None  # None = default to "post" type


class PostUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    slug: str | None = Field(None, max_length=255, pattern=SLUG_PATTERN)
    content_json: dict | None = None
    content_html: str | None = None
    excerpt: str | None = Field(None, max_length=500)
    cover_image_url: str | None = None
    cover_image_alt: str | None = Field(None, max_length=255)
    seo_title: str | None = Field(None, max_length=70)
    seo_description: str | None = Field(None, max_length=170)
    og_image_url: str | None = None
    category_id: UUID | None = None
    tags: list[str] | None = None
    is_featured: bool | None = None
    post_type_id: UUID | None = None  # None = unchanged (exclude_unset)


class AuthorInfo(BaseModel):
    id: UUID
    username: str


class CategoryInfo(BaseModel):
    id: UUID
    name: str
    slug: str


class PostListItem(BaseModel):
    id: UUID
    title: str
    slug: str
    excerpt: str | None
    cover_image_url: str | None
    category: CategoryInfo | None
    post_type: PostTypeInfo | None
    author: AuthorInfo
    status: str
    published_at: datetime | None
    reading_time_min: int | None
    is_featured: bool
    created_at: datetime
    updated_at: datetime


class PostDetail(PostListItem):
    content_json: dict | None
    content_html: str | None
    cover_image_alt: str | None
    seo_title: str | None
    seo_description: str | None
    og_image_url: str | None
    tags: list[str]
    previous_url: str | None = None


class PostListResponse(BaseModel):
    posts: list[PostListItem]
    total: int
    page: int
    per_page: int


# --- Images ---

class ImageResponse(BaseModel):
    id: UUID
    url: str
    alt_text: str | None
    file_size: int | None
    width: int | None
    height: int | None
    post_id: UUID | None = None
    created_at: datetime | None = None


class ImageUpdate(BaseModel):
    alt_text: str | None = Field(None, max_length=255)


class ImageListResponse(BaseModel):
    images: list[ImageResponse]
    total: int
    page: int
    per_page: int


# --- Redirect Resolution ---

class RedirectResponse(BaseModel):
    redirect_to: str
