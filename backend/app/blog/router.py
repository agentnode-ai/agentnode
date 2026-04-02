import math
import re
import uuid
from datetime import datetime, timezone

from io import BytesIO

from fastapi import APIRouter, Depends, Query, Request, UploadFile, File
from PIL import Image as PILImage
from sqlalchemy import extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin.models import AdminAuditLog
from app.auth.dependencies import require_admin
from app.auth.models import User
from app.blog.models import BlogCategory, BlogImage, BlogPost, BlogPostType
from app.blog.schemas import (
    AttachmentFilter,
    BulkDeleteRequest,
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    ImageListResponse,
    ImageResponse,
    ImageSortBy,
    ImageUpdate,
    PostCreate,
    PostDetail,
    PostListItem,
    PostListResponse,
    PostTypeCreate,
    PostTypeResponse,
    PostTypeUpdate,
    PostUpdate,
    RedirectResponse,
    SortOrder,
)
from app.database import get_session
from app.shared.constants import RESERVED_URL_PREFIXES
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit
from app.shared.storage import delete_artifact, upload_artifact
from app.config import settings


# ─── Admin Router ───

admin_router = APIRouter(prefix="/v1/admin/blog", tags=["admin-blog"])


def _estimate_reading_time(html: str | None) -> int:
    """Estimate reading time from HTML content (words / 200 wpm)."""
    if not html:
        return 1
    text = re.sub(r"<[^>]+>", " ", html)
    words = len(text.split())
    return max(1, math.ceil(words / 200))


def _post_type_info(pt: BlogPostType | None) -> dict | None:
    if not pt:
        return None
    return {"id": pt.id, "name": pt.name, "slug": pt.slug, "url_prefix": pt.url_prefix}


def _post_to_list_item(post: BlogPost) -> dict:
    return {
        "id": post.id,
        "title": post.title,
        "slug": post.slug,
        "excerpt": post.excerpt,
        "cover_image_url": post.cover_image_url,
        "category": {"id": post.category.id, "name": post.category.name, "slug": post.category.slug} if post.category else None,
        "post_type": _post_type_info(post.post_type),
        "author": {"id": post.author.id, "username": post.author.username},
        "status": post.status,
        "published_at": post.published_at,
        "reading_time_min": post.reading_time_min,
        "is_featured": post.is_featured,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


def _post_to_detail(post: BlogPost) -> dict:
    d = _post_to_list_item(post)
    d.update({
        "content_json": post.content_json,
        "content_html": post.content_html,
        "cover_image_alt": post.cover_image_alt,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "og_image_url": post.og_image_url,
        "tags": post.tags or [],
        "previous_url": post.previous_url,
    })
    return d


def _load_post_query():
    return select(BlogPost).options(
        selectinload(BlogPost.category),
        selectinload(BlogPost.author),
        selectinload(BlogPost.post_type),
    )


async def _get_default_post_type_id(session: AsyncSession) -> uuid.UUID:
    result = await session.execute(
        select(BlogPostType.id).where(BlogPostType.slug == "post")
    )
    pt_id = result.scalar_one_or_none()
    if not pt_id:
        raise AppError("POST_TYPE_NOT_FOUND", "Default post type 'post' not found", 500)
    return pt_id


# --- Post Types ---

@admin_router.get("/post-types", response_model=list[PostTypeResponse], dependencies=[Depends(rate_limit(30, 60))])
async def list_post_types_admin(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(BlogPostType).order_by(BlogPostType.sort_order, BlogPostType.name)
    )
    types = result.scalars().all()

    count_result = await session.execute(
        select(BlogPost.post_type_id, func.count(BlogPost.id))
        .group_by(BlogPost.post_type_id)
    )
    counts = dict(count_result.all())

    return [
        PostTypeResponse(
            id=t.id, name=t.name, slug=t.slug, url_prefix=t.url_prefix,
            description=t.description, icon=t.icon, has_archive=t.has_archive,
            is_system=t.is_system, archive_title=t.archive_title,
            archive_description=t.archive_description,
            sitemap_priority=t.sitemap_priority, sitemap_changefreq=t.sitemap_changefreq,
            sort_order=t.sort_order, post_count=counts.get(t.id, 0),
            created_at=t.created_at, updated_at=t.updated_at,
        )
        for t in types
    ]


@admin_router.post("/post-types", response_model=PostTypeResponse, dependencies=[Depends(rate_limit(10, 60))])
async def create_post_type(
    body: PostTypeCreate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if body.url_prefix in RESERVED_URL_PREFIXES:
        raise AppError("RESERVED_PREFIX", f"URL prefix '{body.url_prefix}' is reserved", 400)

    existing = await session.execute(
        select(BlogPostType).where(
            (BlogPostType.slug == body.slug) | (BlogPostType.url_prefix == body.url_prefix)
        )
    )
    if existing.scalar_one_or_none():
        raise AppError("POST_TYPE_EXISTS", "Post type with this slug or url_prefix already exists", 409)

    pt = BlogPostType(
        name=body.name, slug=body.slug, url_prefix=body.url_prefix,
        description=body.description, icon=body.icon, has_archive=body.has_archive,
        archive_title=body.archive_title, archive_description=body.archive_description,
        sitemap_priority=body.sitemap_priority, sitemap_changefreq=body.sitemap_changefreq,
        sort_order=body.sort_order,
    )
    session.add(pt)
    await session.commit()
    await session.refresh(pt)

    return PostTypeResponse(
        id=pt.id, name=pt.name, slug=pt.slug, url_prefix=pt.url_prefix,
        description=pt.description, icon=pt.icon, has_archive=pt.has_archive,
        is_system=pt.is_system, archive_title=pt.archive_title,
        archive_description=pt.archive_description,
        sitemap_priority=pt.sitemap_priority, sitemap_changefreq=pt.sitemap_changefreq,
        sort_order=pt.sort_order, post_count=0,
        created_at=pt.created_at, updated_at=pt.updated_at,
    )


@admin_router.put("/post-types/{pt_id}", response_model=PostTypeResponse, dependencies=[Depends(rate_limit(10, 60))])
async def update_post_type(
    pt_id: uuid.UUID,
    body: PostTypeUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BlogPostType).where(BlogPostType.id == pt_id))
    pt = result.scalar_one_or_none()
    if not pt:
        raise AppError("POST_TYPE_NOT_FOUND", "Post type not found", 404)

    # Count posts to enforce slug/url_prefix lock
    count_result = await session.execute(
        select(func.count(BlogPost.id)).where(BlogPost.post_type_id == pt.id)
    )
    post_count = count_result.scalar() or 0

    update_data = body.model_dump(exclude_unset=True)

    # Lock slug and url_prefix if posts assigned
    if post_count > 0:
        if "slug" in update_data and update_data["slug"] != pt.slug:
            raise AppError("SLUG_LOCKED", "Cannot change slug when posts are assigned to this type", 400)
        if "url_prefix" in update_data and update_data["url_prefix"] != pt.url_prefix:
            raise AppError("PREFIX_LOCKED", "Cannot change url_prefix when posts are assigned to this type", 400)

    # Check reserved prefix
    if "url_prefix" in update_data and update_data["url_prefix"] in RESERVED_URL_PREFIXES:
        raise AppError("RESERVED_PREFIX", f"URL prefix '{update_data['url_prefix']}' is reserved", 400)

    for key, value in update_data.items():
        setattr(pt, key, value)

    pt.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(pt)

    return PostTypeResponse(
        id=pt.id, name=pt.name, slug=pt.slug, url_prefix=pt.url_prefix,
        description=pt.description, icon=pt.icon, has_archive=pt.has_archive,
        is_system=pt.is_system, archive_title=pt.archive_title,
        archive_description=pt.archive_description,
        sitemap_priority=pt.sitemap_priority, sitemap_changefreq=pt.sitemap_changefreq,
        sort_order=pt.sort_order, post_count=post_count,
        created_at=pt.created_at, updated_at=pt.updated_at,
    )


@admin_router.delete("/post-types/{pt_id}", dependencies=[Depends(rate_limit(5, 60))])
async def delete_post_type(
    pt_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BlogPostType).where(BlogPostType.id == pt_id))
    pt = result.scalar_one_or_none()
    if not pt:
        raise AppError("POST_TYPE_NOT_FOUND", "Post type not found", 404)

    if pt.is_system:
        raise AppError("SYSTEM_TYPE", "System post types cannot be deleted", 400)

    count_result = await session.execute(
        select(func.count(BlogPost.id)).where(BlogPost.post_type_id == pt.id)
    )
    if (count_result.scalar() or 0) > 0:
        raise AppError("TYPE_HAS_POSTS", "Cannot delete post type with assigned posts", 400)

    await session.delete(pt)
    await session.commit()
    return {"ok": True}


# --- Categories ---

@admin_router.get("/categories", response_model=list[CategoryResponse], dependencies=[Depends(rate_limit(30, 60))])
async def list_categories(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(BlogCategory).order_by(BlogCategory.sort_order, BlogCategory.name)
    )
    categories = result.scalars().all()

    # Count posts per category
    count_result = await session.execute(
        select(BlogPost.category_id, func.count(BlogPost.id))
        .group_by(BlogPost.category_id)
    )
    counts = dict(count_result.all())

    return [
        CategoryResponse(
            id=c.id, name=c.name, slug=c.slug,
            description=c.description, sort_order=c.sort_order,
            post_count=counts.get(c.id, 0),
        )
        for c in categories
    ]


@admin_router.post("/categories", response_model=CategoryResponse, dependencies=[Depends(rate_limit(10, 60))])
async def create_category(
    body: CategoryCreate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    existing = await session.execute(select(BlogCategory).where(BlogCategory.slug == body.slug))
    if existing.scalar_one_or_none():
        raise AppError("BLOG_CATEGORY_EXISTS", "Category with this slug already exists", 409)

    cat = BlogCategory(name=body.name, slug=body.slug, description=body.description, sort_order=body.sort_order)
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return CategoryResponse(id=cat.id, name=cat.name, slug=cat.slug, description=cat.description, sort_order=cat.sort_order, post_count=0)


@admin_router.put("/categories/{cat_id}", response_model=CategoryResponse, dependencies=[Depends(rate_limit(10, 60))])
async def update_category(
    cat_id: uuid.UUID,
    body: CategoryUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BlogCategory).where(BlogCategory.id == cat_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise AppError("BLOG_CATEGORY_NOT_FOUND", "Category not found", 404)

    if body.name is not None:
        cat.name = body.name
    if body.slug is not None:
        cat.slug = body.slug
    if body.description is not None:
        cat.description = body.description
    if body.sort_order is not None:
        cat.sort_order = body.sort_order

    await session.commit()
    await session.refresh(cat)

    count_result = await session.execute(select(func.count(BlogPost.id)).where(BlogPost.category_id == cat.id))
    count = count_result.scalar() or 0

    return CategoryResponse(id=cat.id, name=cat.name, slug=cat.slug, description=cat.description, sort_order=cat.sort_order, post_count=count)


@admin_router.delete("/categories/{cat_id}", dependencies=[Depends(rate_limit(5, 60))])
async def delete_category(
    cat_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BlogCategory).where(BlogCategory.id == cat_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise AppError("BLOG_CATEGORY_NOT_FOUND", "Category not found", 404)

    await session.delete(cat)
    await session.commit()
    return {"ok": True}


# --- Posts ---

@admin_router.get("/posts", response_model=PostListResponse, dependencies=[Depends(rate_limit(30, 60))])
async def list_posts_admin(
    status: str | None = Query(None),
    category_id: uuid.UUID | None = Query(None),
    post_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = _load_post_query()
    count_q = select(func.count(BlogPost.id))

    if status:
        q = q.where(BlogPost.status == status)
        count_q = count_q.where(BlogPost.status == status)
    if category_id:
        q = q.where(BlogPost.category_id == category_id)
        count_q = count_q.where(BlogPost.category_id == category_id)
    if post_type:
        q = q.join(BlogPostType).where(BlogPostType.slug == post_type)
        count_q = count_q.join(BlogPostType).where(BlogPostType.slug == post_type)

    total = (await session.execute(count_q)).scalar() or 0
    q = q.order_by(BlogPost.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await session.execute(q)
    posts = result.scalars().all()

    return PostListResponse(
        posts=[PostListItem(**_post_to_list_item(p)) for p in posts],
        total=total, page=page, per_page=per_page,
    )


@admin_router.post("/posts", response_model=PostDetail, dependencies=[Depends(rate_limit(10, 60))])
async def create_post(
    body: PostCreate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    existing = await session.execute(select(BlogPost).where(BlogPost.slug == body.slug))
    if existing.scalar_one_or_none():
        raise AppError("BLOG_POST_EXISTS", "Post with this slug already exists", 409)

    # Resolve post_type_id: use provided or default to "post"
    post_type_id = body.post_type_id or await _get_default_post_type_id(session)

    post = BlogPost(
        title=body.title,
        slug=body.slug,
        content_json=body.content_json,
        content_html=body.content_html,
        excerpt=body.excerpt,
        cover_image_url=body.cover_image_url,
        cover_image_alt=body.cover_image_alt,
        seo_title=body.seo_title,
        seo_description=body.seo_description,
        og_image_url=body.og_image_url,
        category_id=body.category_id,
        tags=body.tags,
        author_id=user.id,
        is_featured=body.is_featured,
        reading_time_min=_estimate_reading_time(body.content_html),
        post_type_id=post_type_id,
    )
    session.add(post)
    await session.commit()

    # Reload with relationships
    result = await session.execute(_load_post_query().where(BlogPost.id == post.id))
    post = result.scalar_one()
    return PostDetail(**_post_to_detail(post))


@admin_router.get("/posts/{post_id}", response_model=PostDetail, dependencies=[Depends(rate_limit(30, 60))])
async def get_post_admin(
    post_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(_load_post_query().where(BlogPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise AppError("BLOG_POST_NOT_FOUND", "Post not found", 404)
    return PostDetail(**_post_to_detail(post))


@admin_router.put("/posts/{post_id}", response_model=PostDetail, dependencies=[Depends(rate_limit(20, 60))])
async def update_post(
    post_id: uuid.UUID,
    body: PostUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(_load_post_query().where(BlogPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise AppError("BLOG_POST_NOT_FOUND", "Post not found", 404)

    update_data = body.model_dump(exclude_unset=True)

    # Handle post_type_id change — set previous_url redirect
    if "post_type_id" in update_data:
        new_type_id = update_data["post_type_id"]
        if new_type_id is None:
            # null sent in payload = treat as unset, do not change
            del update_data["post_type_id"]
        elif new_type_id != post.post_type_id:
            # Type is changing — save old URL as previous_url
            old_prefix = post.post_type.url_prefix if post.post_type else "blog"
            post.previous_url = f"/{old_prefix}/{post.slug}"

    for key, value in update_data.items():
        setattr(post, key, value)

    if "content_html" in update_data:
        post.reading_time_min = _estimate_reading_time(post.content_html)

    post.updated_at = datetime.now(timezone.utc)
    await session.commit()

    # Reload
    result = await session.execute(_load_post_query().where(BlogPost.id == post.id))
    post = result.scalar_one()
    return PostDetail(**_post_to_detail(post))


@admin_router.delete("/posts/{post_id}", dependencies=[Depends(rate_limit(5, 60))])
async def delete_post(
    post_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BlogPost).where(BlogPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise AppError("BLOG_POST_NOT_FOUND", "Post not found", 404)

    await session.delete(post)
    await session.commit()
    return {"ok": True}


@admin_router.post("/posts/{post_id}/publish", response_model=PostDetail, dependencies=[Depends(rate_limit(10, 60))])
async def publish_post(
    post_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(_load_post_query().where(BlogPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise AppError("BLOG_POST_NOT_FOUND", "Post not found", 404)

    post.status = "published"
    if not post.published_at:
        post.published_at = datetime.now(timezone.utc)
    post.updated_at = datetime.now(timezone.utc)
    await session.commit()

    result = await session.execute(_load_post_query().where(BlogPost.id == post.id))
    post = result.scalar_one()
    return PostDetail(**_post_to_detail(post))


@admin_router.post("/posts/{post_id}/unpublish", response_model=PostDetail, dependencies=[Depends(rate_limit(10, 60))])
async def unpublish_post(
    post_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(_load_post_query().where(BlogPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise AppError("BLOG_POST_NOT_FOUND", "Post not found", 404)

    post.status = "draft"
    post.updated_at = datetime.now(timezone.utc)
    await session.commit()

    result = await session.execute(_load_post_query().where(BlogPost.id == post.id))
    post = result.scalar_one()
    return PostDetail(**_post_to_detail(post))


# --- Image Upload ---

@admin_router.post("/images/upload", response_model=ImageResponse, dependencies=[Depends(rate_limit(20, 60))])
async def upload_image(
    file: UploadFile = File(...),
    post_id: uuid.UUID | None = Query(None),
    alt_text: str | None = Query(None),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise AppError("BLOG_INVALID_FILE", "Only image files are allowed", 400)

    # Restrict file extensions to prevent uploading disguised files
    ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "avif"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "jpg"
    if ext not in ALLOWED_IMAGE_EXTS:
        raise AppError("BLOG_INVALID_FILE", f"File extension '.{ext}' not allowed. Use: {', '.join(sorted(ALLOWED_IMAGE_EXTS))}", 400)

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:  # 10 MB
        raise AppError("BLOG_FILE_TOO_LARGE", "Image must be under 10 MB", 400)

    # Extract dimensions via Pillow (non-fatal for SVG/ICO etc.)
    width, height = None, None
    try:
        pil_img = PILImage.open(BytesIO(data))
        width, height = pil_img.size
        pil_img.close()
    except Exception:
        pass

    original_filename = file.filename

    image_id = uuid.uuid4()
    object_key = f"blog/{image_id}.{ext}"

    await upload_artifact(object_key, data, content_type=file.content_type)

    # Build public URL
    public_endpoint = settings.S3_PUBLIC_ENDPOINT or settings.S3_ENDPOINT
    url = f"{public_endpoint}/{settings.S3_BUCKET}/{object_key}"

    image = BlogImage(
        id=image_id,
        post_id=post_id,
        object_key=object_key,
        url=url,
        alt_text=alt_text,
        file_size=len(data),
        width=width,
        height=height,
        original_filename=original_filename,
    )
    session.add(image)
    await session.commit()
    await session.refresh(image)

    return ImageResponse(
        id=image.id, url=image.url, alt_text=image.alt_text,
        file_size=image.file_size, width=image.width, height=image.height,
        title=image.title, original_filename=image.original_filename,
        caption=image.caption, post_id=image.post_id, created_at=image.created_at,
    )


# --- Image Management ---

@admin_router.get("/images", response_model=ImageListResponse, dependencies=[Depends(rate_limit(30, 60))])
async def list_images(
    search: str | None = Query(None),
    sort_by: ImageSortBy | None = Query(None),
    sort_order: SortOrder | None = Query(None),
    month: str | None = Query(None),
    attachment: AttachmentFilter | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(40, ge=1, le=100),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = select(BlogImage)
    count_q = select(func.count(BlogImage.id))

    # Search across alt_text, title, original_filename
    if search:
        like = f"%{search}%"
        flt = or_(
            BlogImage.alt_text.ilike(like),
            BlogImage.title.ilike(like),
            BlogImage.original_filename.ilike(like),
        )
        q = q.where(flt)
        count_q = count_q.where(flt)

    # Month filter (YYYY-MM)
    if month:
        try:
            parts = month.split("-")
            y, m = int(parts[0]), int(parts[1])
            if not (1 <= m <= 12 and 2000 <= y <= 2100):
                raise ValueError
        except (ValueError, IndexError):
            raise AppError("INVALID_MONTH", "Month must be YYYY-MM (e.g. 2026-03)", 422)
        q = q.where(
            extract("year", BlogImage.created_at) == y,
            extract("month", BlogImage.created_at) == m,
        )
        count_q = count_q.where(
            extract("year", BlogImage.created_at) == y,
            extract("month", BlogImage.created_at) == m,
        )

    # Attachment filter
    if attachment == AttachmentFilter.attached:
        q = q.where(BlogImage.post_id.isnot(None))
        count_q = count_q.where(BlogImage.post_id.isnot(None))
    elif attachment == AttachmentFilter.unattached:
        q = q.where(BlogImage.post_id.is_(None))
        count_q = count_q.where(BlogImage.post_id.is_(None))

    total = (await session.execute(count_q)).scalar() or 0

    # Sort
    col = BlogImage.file_size if sort_by == ImageSortBy.file_size else BlogImage.created_at
    order = col.asc() if sort_order == SortOrder.asc else col.desc()
    q = q.order_by(order)

    q = q.offset((page - 1) * per_page).limit(per_page)
    result = await session.execute(q)
    images = result.scalars().all()

    return ImageListResponse(
        images=[
            ImageResponse(
                id=img.id, url=img.url, alt_text=img.alt_text,
                file_size=img.file_size, width=img.width, height=img.height,
                title=img.title, original_filename=img.original_filename,
                caption=img.caption, post_id=img.post_id, created_at=img.created_at,
            )
            for img in images
        ],
        total=total, page=page, per_page=per_page,
    )


@admin_router.get("/images/months", dependencies=[Depends(rate_limit(30, 60))])
async def list_image_months(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(
            extract("year", BlogImage.created_at).label("y"),
            extract("month", BlogImage.created_at).label("m"),
        )
        .group_by("y", "m")
        .order_by(extract("year", BlogImage.created_at).desc(), extract("month", BlogImage.created_at).desc())
    )
    rows = result.all()

    month_names = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    return [
        {"value": f"{int(r.y)}-{int(r.m):02d}", "label": f"{month_names[int(r.m)]} {int(r.y)}"}
        for r in rows
    ]


@admin_router.put("/images/{image_id}", response_model=ImageResponse, dependencies=[Depends(rate_limit(20, 60))])
async def update_image(
    image_id: uuid.UUID,
    body: ImageUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BlogImage).where(BlogImage.id == image_id))
    image = result.scalar_one_or_none()
    if not image:
        raise AppError("BLOG_IMAGE_NOT_FOUND", "Image not found", 404)

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(image, key, value)

    await session.commit()
    await session.refresh(image)
    return ImageResponse(
        id=image.id, url=image.url, alt_text=image.alt_text,
        file_size=image.file_size, width=image.width, height=image.height,
        title=image.title, original_filename=image.original_filename,
        caption=image.caption, post_id=image.post_id, created_at=image.created_at,
    )


@admin_router.delete("/images/{image_id}", dependencies=[Depends(rate_limit(10, 60))])
async def delete_image(
    image_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(BlogImage).where(BlogImage.id == image_id))
    image = result.scalar_one_or_none()
    if not image:
        raise AppError("BLOG_IMAGE_NOT_FOUND", "Image not found", 404)

    # Delete from S3
    try:
        await delete_artifact(image.object_key)
    except Exception:
        pass  # Continue even if S3 delete fails

    await session.delete(image)
    await session.commit()
    return {"ok": True}


@admin_router.post("/images/bulk-delete", dependencies=[Depends(rate_limit(5, 60))])
async def bulk_delete_images(
    body: BulkDeleteRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(BlogImage).where(BlogImage.id.in_(body.ids))
    )
    found = list(result.scalars().all())
    found_ids = {img.id for img in found}
    not_found = len(body.ids) - len(found_ids)

    deletable = [img for img in found if img.post_id is None]
    skipped_attached = len(found) - len(deletable)

    for img in deletable:
        try:
            await delete_artifact(img.object_key)
        except Exception:
            pass
        await session.delete(img)

    await session.commit()

    return {
        "ok": True,
        "deleted": len(deletable),
        "skipped_attached": skipped_attached,
        "not_found": not_found,
    }


# ─── Public Router ───

public_router = APIRouter(prefix="/v1/blog", tags=["blog"])


@public_router.get("/post-types", response_model=list[PostTypeResponse])
async def list_post_types_public(
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(BlogPostType).order_by(BlogPostType.sort_order, BlogPostType.name)
    )
    types = result.scalars().all()

    count_result = await session.execute(
        select(BlogPost.post_type_id, func.count(BlogPost.id))
        .where(BlogPost.status == "published")
        .group_by(BlogPost.post_type_id)
    )
    counts = dict(count_result.all())

    return [
        PostTypeResponse(
            id=t.id, name=t.name, slug=t.slug, url_prefix=t.url_prefix,
            description=t.description, icon=t.icon, has_archive=t.has_archive,
            is_system=t.is_system, archive_title=t.archive_title,
            archive_description=t.archive_description,
            sitemap_priority=t.sitemap_priority, sitemap_changefreq=t.sitemap_changefreq,
            sort_order=t.sort_order, post_count=counts.get(t.id, 0),
            created_at=t.created_at, updated_at=t.updated_at,
        )
        for t in types
    ]


@public_router.get("/posts", response_model=PostListResponse)
async def list_posts_public(
    category: str | None = Query(None),
    tag: str | None = Query(None),
    post_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    q = _load_post_query().where(BlogPost.status == "published")
    count_q = select(func.count(BlogPost.id)).where(BlogPost.status == "published")

    if category:
        q = q.join(BlogCategory).where(BlogCategory.slug == category)
        count_q = count_q.join(BlogCategory).where(BlogCategory.slug == category)
    if tag:
        q = q.where(BlogPost.tags.contains([tag]))
        count_q = count_q.where(BlogPost.tags.contains([tag]))
    if post_type:
        q = q.join(BlogPostType).where(BlogPostType.slug == post_type)
        count_q = count_q.join(BlogPostType).where(BlogPostType.slug == post_type)

    total = (await session.execute(count_q)).scalar() or 0
    q = q.order_by(BlogPost.published_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await session.execute(q)
    posts = result.scalars().all()

    return PostListResponse(
        posts=[PostListItem(**_post_to_list_item(p)) for p in posts],
        total=total, page=page, per_page=per_page,
    )


@public_router.get("/posts/{slug}", response_model=PostDetail)
async def get_post_public(
    slug: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        _load_post_query().where(BlogPost.slug == slug, BlogPost.status == "published")
    )
    post = result.scalar_one_or_none()
    if not post:
        raise AppError("BLOG_POST_NOT_FOUND", "Post not found", 404)
    return PostDetail(**_post_to_detail(post))


@public_router.get("/resolve", response_model=RedirectResponse)
async def resolve_redirect(
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    # Normalize path: strip trailing slash, no query string
    normalized = path.rstrip("/").split("?")[0]
    if not normalized.startswith("/"):
        normalized = "/" + normalized

    result = await session.execute(
        _load_post_query().where(
            BlogPost.previous_url == normalized,
            BlogPost.status == "published",
        )
    )
    post = result.scalar_one_or_none()
    if not post:
        raise AppError("REDIRECT_NOT_FOUND", "No redirect found for this path", 404)

    new_prefix = post.post_type.url_prefix if post.post_type else "blog"
    return RedirectResponse(redirect_to=f"/{new_prefix}/{post.slug}")


@public_router.get("/categories", response_model=list[CategoryResponse])
async def list_categories_public(
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(BlogCategory).order_by(BlogCategory.sort_order, BlogCategory.name)
    )
    categories = result.scalars().all()

    count_result = await session.execute(
        select(BlogPost.category_id, func.count(BlogPost.id))
        .where(BlogPost.status == "published")
        .group_by(BlogPost.category_id)
    )
    counts = dict(count_result.all())

    return [
        CategoryResponse(
            id=c.id, name=c.name, slug=c.slug,
            description=c.description, sort_order=c.sort_order,
            post_count=counts.get(c.id, 0),
        )
        for c in categories
    ]
