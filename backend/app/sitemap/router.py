import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.auth.models import User
from app.blog.models import BlogPost, BlogPostType
from app.database import get_session
from app.packages.models import Package
from app.publishers.models import Publisher
from app.shared.rate_limit import rate_limit
from app.shared.exceptions import AppError
from app.sitemap.models import SitemapPage

logger = logging.getLogger(__name__)

# P1-D5: cap how many rows we return per sitemap endpoint so a crawler cannot
# pull the entire table in one request. 50_000 URLs per sitemap file is the
# sitemap.org protocol max; we add a pagination page= query param so the
# frontend's sitemap index can shard packages/posts across multiple sitemaps
# if we ever exceed that ceiling. Results are cached in Redis for 10 minutes.
SITEMAP_PAGE_SIZE = 5000
SITEMAP_CACHE_TTL = 600  # seconds


async def _cached(request: Request, key: str, builder):
    """Simple redis-backed JSON cache. Falls back to builder() on any error."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return await builder()
    try:
        raw = await redis.get(key)
        if raw:
            return json.loads(raw)
    except Exception:
        logger.debug("sitemap cache read failed for %s", key)
    data = await builder()
    try:
        await redis.set(key, json.dumps(data, default=str), ex=SITEMAP_CACHE_TTL)
    except Exception:
        logger.debug("sitemap cache write failed for %s", key)
    return data


# --- Schemas ---

class SitemapPageCreate(BaseModel):
    path: str = Field(max_length=300, pattern=r"^/.*$")
    priority: Decimal = Decimal("0.5")
    changefreq: str = "monthly"
    indexable: bool = True
    sort_order: int = 0


class SitemapPageUpdate(BaseModel):
    path: str | None = Field(None, max_length=300, pattern=r"^/.*$")
    priority: Decimal | None = None
    changefreq: str | None = None
    indexable: bool | None = None
    sort_order: int | None = None


class SitemapPageResponse(BaseModel):
    id: uuid.UUID
    path: str
    priority: Decimal
    changefreq: str
    indexable: bool
    sort_order: int


# --- Public Router (no rate limit for crawlers) ---

router = APIRouter(prefix="/v1/sitemap", tags=["sitemap"])


@router.get("/posts/{post_type_slug}", dependencies=[Depends(rate_limit(60, 60))])
async def sitemap_posts(
    post_type_slug: str,
    request: Request,
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    async def build():
        pt_result = await session.execute(
            select(BlogPostType).where(BlogPostType.slug == post_type_slug)
        )
        pt = pt_result.scalar_one_or_none()
        if not pt:
            return {"items": [], "page": page, "page_size": SITEMAP_PAGE_SIZE, "total": 0}

        total = (await session.execute(
            select(func.count(BlogPost.id)).where(
                BlogPost.post_type_id == pt.id, BlogPost.status == "published"
            )
        )).scalar() or 0

        result = await session.execute(
            select(BlogPost.slug, BlogPost.updated_at)
            .where(BlogPost.post_type_id == pt.id, BlogPost.status == "published")
            .order_by(BlogPost.published_at.desc())
            .offset((page - 1) * SITEMAP_PAGE_SIZE)
            .limit(SITEMAP_PAGE_SIZE)
        )
        rows = result.all()

        return {
            "items": [
                {"slug": r.slug, "updated_at": r.updated_at.isoformat() if r.updated_at else None, "url_prefix": pt.url_prefix}
                for r in rows
            ],
            "page": page,
            "page_size": SITEMAP_PAGE_SIZE,
            "total": total,
        }

    return await _cached(request, f"sitemap:posts:{post_type_slug}:{page}", build)


@router.get("/packages", dependencies=[Depends(rate_limit(60, 60))])
async def sitemap_packages(
    request: Request,
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    async def build():
        total = (await session.execute(
            select(func.count(Package.id)).where(Package.is_deprecated == False)  # noqa: E712
        )).scalar() or 0

        result = await session.execute(
            select(Package.slug, Package.updated_at)
            .where(Package.is_deprecated == False)  # noqa: E712
            .order_by(Package.updated_at.desc())
            .offset((page - 1) * SITEMAP_PAGE_SIZE)
            .limit(SITEMAP_PAGE_SIZE)
        )
        rows = result.all()

        return {
            "items": [
                {"slug": r.slug, "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                for r in rows
            ],
            "page": page,
            "page_size": SITEMAP_PAGE_SIZE,
            "total": total,
        }

    return await _cached(request, f"sitemap:packages:{page}", build)


@router.get("/publishers", dependencies=[Depends(rate_limit(60, 60))])
async def sitemap_publishers(
    request: Request,
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
):
    async def build():
        total = (await session.execute(
            select(func.count(Publisher.id)).where(Publisher.is_suspended == False)  # noqa: E712
        )).scalar() or 0

        result = await session.execute(
            select(Publisher.slug, Publisher.updated_at)
            .where(Publisher.is_suspended == False)  # noqa: E712
            .order_by(Publisher.updated_at.desc())
            .offset((page - 1) * SITEMAP_PAGE_SIZE)
            .limit(SITEMAP_PAGE_SIZE)
        )
        rows = result.all()

        return {
            "items": [
                {"slug": r.slug, "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                for r in rows
            ],
            "page": page,
            "page_size": SITEMAP_PAGE_SIZE,
            "total": total,
        }

    return await _cached(request, f"sitemap:publishers:{page}", build)


@router.get("/pages", dependencies=[Depends(rate_limit(60, 60))])
async def sitemap_pages(
    session: AsyncSession = Depends(get_session),
):
    # Static pages from DB (only indexable)
    result = await session.execute(
        select(SitemapPage)
        .where(SitemapPage.indexable == True)
        .order_by(SitemapPage.sort_order)
    )
    db_pages = result.scalars().all()

    pages = [
        {"path": p.path, "priority": float(p.priority), "changefreq": p.changefreq}
        for p in db_pages
    ]

    # Add archive pages for post types with has_archive=true
    result = await session.execute(
        select(BlogPostType).where(BlogPostType.has_archive == True).order_by(BlogPostType.sort_order)
    )
    post_types = result.scalars().all()

    for pt in post_types:
        pages.append({
            "path": f"/{pt.url_prefix}",
            "priority": float(pt.sitemap_priority),
            "changefreq": pt.sitemap_changefreq,
        })

    return {"items": pages}


# --- Admin Router ---

admin_router = APIRouter(prefix="/v1/admin/sitemap", tags=["admin-sitemap"])


@admin_router.get("/pages", response_model=list[SitemapPageResponse], dependencies=[Depends(rate_limit(30, 60))])
async def list_sitemap_pages(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(SitemapPage).order_by(SitemapPage.sort_order, SitemapPage.path)
    )
    pages = result.scalars().all()
    return [
        SitemapPageResponse(
            id=p.id, path=p.path, priority=p.priority,
            changefreq=p.changefreq, indexable=p.indexable, sort_order=p.sort_order,
        )
        for p in pages
    ]


@admin_router.post("/pages", response_model=SitemapPageResponse, dependencies=[Depends(rate_limit(10, 60))])
async def create_sitemap_page(
    body: SitemapPageCreate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    existing = await session.execute(
        select(SitemapPage).where(SitemapPage.path == body.path)
    )
    if existing.scalar_one_or_none():
        raise AppError("SITEMAP_PAGE_EXISTS", "Page with this path already exists", 409)

    page = SitemapPage(
        path=body.path, priority=body.priority, changefreq=body.changefreq,
        indexable=body.indexable, sort_order=body.sort_order,
    )
    session.add(page)
    await session.commit()
    await session.refresh(page)
    return SitemapPageResponse(
        id=page.id, path=page.path, priority=page.priority,
        changefreq=page.changefreq, indexable=page.indexable, sort_order=page.sort_order,
    )


@admin_router.put("/pages/{page_id}", response_model=SitemapPageResponse, dependencies=[Depends(rate_limit(20, 60))])
async def update_sitemap_page(
    page_id: uuid.UUID,
    body: SitemapPageUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(SitemapPage).where(SitemapPage.id == page_id))
    page = result.scalar_one_or_none()
    if not page:
        raise AppError("SITEMAP_PAGE_NOT_FOUND", "Sitemap page not found", 404)

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(page, key, value)

    page.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(page)
    return SitemapPageResponse(
        id=page.id, path=page.path, priority=page.priority,
        changefreq=page.changefreq, indexable=page.indexable, sort_order=page.sort_order,
    )


@admin_router.delete("/pages/{page_id}", dependencies=[Depends(rate_limit(5, 60))])
async def delete_sitemap_page(
    page_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(SitemapPage).where(SitemapPage.id == page_id))
    page = result.scalar_one_or_none()
    if not page:
        raise AppError("SITEMAP_PAGE_NOT_FOUND", "Sitemap page not found", 404)

    await session.delete(page)
    await session.commit()
    return {"ok": True}
