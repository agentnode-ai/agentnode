import hashlib
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request

from app.config import settings
from app.shared.rate_limit import rate_limit
from app.search.schemas import SearchHit, SearchRequest, SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["search"])

MEILI_INDEX = "packages"

# Default sort when no query text — spec §8.2
DEFAULT_SORT = "download_count:desc"

# Shared httpx client pool — reuses TCP connections across requests
_search_client: httpx.AsyncClient | None = None


def _get_search_client() -> httpx.AsyncClient:
    global _search_client
    if _search_client is None:
        _search_client = httpx.AsyncClient(timeout=10)
    return _search_client


def _get_search_key() -> str:
    """Return the search-only key if configured, otherwise fall back to master key."""
    if settings.MEILISEARCH_SEARCH_KEY:
        return settings.MEILISEARCH_SEARCH_KEY
    return settings.MEILISEARCH_KEY


def _build_search_cache_key(body: SearchRequest) -> str:
    """Build a deterministic cache key from search parameters."""
    key_parts = {
        "q": body.q,
        "package_type": body.package_type,
        "capability_id": body.capability_id,
        "framework": body.framework,
        "runtime": body.runtime,
        "trust_level": body.trust_level,
        "verification_tier": body.verification_tier,
        "publisher_slug": body.publisher_slug,
        "sort_by": body.sort_by,
        "page": body.page,
        "per_page": body.per_page,
    }
    raw = json.dumps(key_parts, sort_keys=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"search:{digest}"


SEARCH_CACHE_TTL = 60  # seconds


@router.post("/search", response_model=SearchResponse, dependencies=[Depends(rate_limit(30, 60))])
async def search_packages(body: SearchRequest, request: Request):
    """Full-text search over published packages via Meilisearch. Spec §8.2."""
    # Try Redis cache first
    redis = request.app.state.redis
    cache_key = _build_search_cache_key(body)
    try:
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Redis cache read failed for %s", cache_key, exc_info=True)

    q = body.q
    per_page = body.per_page
    page = body.page
    offset = (page - 1) * per_page

    # Build filter clauses
    filters = []
    if body.package_type:
        filters.append(f'package_type = "{body.package_type}"')
    if body.capability_id:
        filters.append(f'capability_ids = "{body.capability_id}"')
    if body.framework:
        filters.append(f'frameworks = "{body.framework}"')
    if body.runtime:
        filters.append(f'runtime = "{body.runtime}"')
    if body.trust_level:
        filters.append(f'trust_level = "{body.trust_level}"')
    if body.publisher_slug:
        filters.append(f'publisher_slug = "{body.publisher_slug}"')
    if body.verification_tier:
        filters.append(f'verification_tier = "{body.verification_tier}"')
    filters.append("is_deprecated = false")

    meili_body: dict = {
        "q": q,
        "limit": per_page,
        "offset": offset,
        "filter": filters,
    }

    # Sort: use explicit sort_by, or default when no query text
    sort_by = body.sort_by
    if sort_by:
        meili_body["sort"] = [sort_by]
    elif not q:
        meili_body["sort"] = [DEFAULT_SORT]

    headers = {"Authorization": f"Bearer {_get_search_key()}"}
    try:
        client = _get_search_client()
        resp = await client.post(
            f"{settings.MEILISEARCH_URL}/indexes/{MEILI_INDEX}/search",
            json=meili_body,
            headers=headers,
        )
        if resp.status_code != 200:
            logger.error(f"Meilisearch search failed: {resp.status_code} {resp.text}")
            return SearchResponse(query=q, hits=[], total=0, page=page, per_page=per_page)

        data = resp.json()
    except Exception as e:
        logger.error(f"Meilisearch search error: {e}")
        return SearchResponse(query=q, hits=[], total=0, page=page, per_page=per_page)

    hits = []
    for doc in data.get("hits", []):
        hits.append(SearchHit(
            slug=doc.get("slug", ""),
            name=doc.get("name", ""),
            package_type=doc.get("package_type", ""),
            summary=doc.get("summary", ""),
            publisher_name=doc.get("publisher_name", ""),
            publisher_slug=doc.get("publisher_slug", ""),
            trust_level=doc.get("trust_level", "unverified"),
            latest_version=doc.get("latest_version"),
            runtime=doc.get("runtime"),
            capability_ids=doc.get("capability_ids", []),
            tags=doc.get("tags", []),
            frameworks=doc.get("frameworks", []),
            download_count=doc.get("download_count", 0),
            is_deprecated=doc.get("is_deprecated", False),
            verification_status=doc.get("verification_status"),
            verification_score=doc.get("verification_score"),
            verification_tier=doc.get("verification_tier"),
        ))

    total = data.get("estimatedTotalHits", data.get("totalHits", len(hits)))

    response = SearchResponse(query=q, hits=hits, total=total, page=page, per_page=per_page)

    # Cache the response
    try:
        await redis.set(cache_key, json.dumps(response.model_dump(mode="json")), ex=SEARCH_CACHE_TTL)
    except Exception:
        logger.warning("Redis cache write failed for %s", cache_key, exc_info=True)

    return response
