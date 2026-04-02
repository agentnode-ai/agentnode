import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

INDEX_NAME = "packages"

# Shared httpx client — reuses TCP connections across requests.
# Initialized via init_meili_client() at app startup, closed via close_meili_client() at shutdown.
_meili_client: httpx.AsyncClient | None = None


def init_meili_client() -> None:
    """Create the shared Meilisearch httpx client. Call once at app startup."""
    global _meili_client
    _meili_client = httpx.AsyncClient(
        base_url=settings.MEILISEARCH_URL,
        headers={"Authorization": f"Bearer {settings.MEILISEARCH_KEY}"},
        timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
    )


async def close_meili_client() -> None:
    """Close the shared Meilisearch httpx client. Call once at app shutdown."""
    global _meili_client
    if _meili_client is not None:
        await _meili_client.aclose()
        _meili_client = None


def get_meili_client() -> httpx.AsyncClient:
    """Return the shared client, lazily initializing if needed (e.g. scripts)."""
    global _meili_client
    if _meili_client is None:
        init_meili_client()
    return _meili_client


async def sync_package_to_meilisearch(document: dict) -> None:
    """Upsert a package document to Meilisearch. Logs on failure, never raises."""
    try:
        client = get_meili_client()
        resp = await client.post(
            f"/indexes/{INDEX_NAME}/documents",
            json=[document],
        )
        if resp.status_code not in (200, 202):
            logger.error(f"Meilisearch sync failed for {document.get('slug')}: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Meilisearch sync error for {document.get('slug')}: {e}")


async def delete_package_from_meilisearch(slug: str) -> None:
    """Delete a package document from Meilisearch. Logs on failure, never raises."""
    try:
        client = get_meili_client()
        resp = await client.delete(
            f"/indexes/{INDEX_NAME}/documents/{slug}",
        )
        if resp.status_code not in (200, 202):
            logger.error(f"Meilisearch delete failed for {slug}: {resp.status_code}")
    except Exception as e:
        logger.error(f"Meilisearch delete error for {slug}: {e}")
