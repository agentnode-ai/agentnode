import logging

import httpx
from fastapi import APIRouter, Depends

from app.config import settings
from app.shared.rate_limit import rate_limit
from app.search.schemas import SearchHit, SearchRequest, SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["search"])

MEILI_INDEX = "packages"

# Default sort when no query text — spec §8.2
DEFAULT_SORT = "download_count:desc"


@router.post("/search", response_model=SearchResponse, dependencies=[Depends(rate_limit(30, 60))])
async def search_packages(body: SearchRequest):
    """Full-text search over published packages via Meilisearch. Spec §8.2."""
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

    headers = {"Authorization": f"Bearer {settings.MEILISEARCH_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.MEILISEARCH_URL}/indexes/{MEILI_INDEX}/search",
                json=meili_body,
                headers=headers,
                timeout=10,
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
        ))

    total = data.get("estimatedTotalHits", data.get("totalHits", len(hits)))

    return SearchResponse(query=q, hits=hits, total=total, page=page, per_page=per_page)
