import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

INDEX_NAME = "packages"


async def sync_package_to_meilisearch(document: dict) -> None:
    """Upsert a package document to Meilisearch. Logs on failure, never raises."""
    try:
        headers = {"Authorization": f"Bearer {settings.MEILISEARCH_KEY}"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.MEILISEARCH_URL}/indexes/{INDEX_NAME}/documents",
                json=[document],
                headers=headers,
                timeout=10,
            )
            if resp.status_code not in (200, 202):
                logger.error(f"Meilisearch sync failed for {document.get('slug')}: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Meilisearch sync error for {document.get('slug')}: {e}")


async def delete_package_from_meilisearch(slug: str) -> None:
    """Delete a package document from Meilisearch. Logs on failure, never raises."""
    try:
        headers = {"Authorization": f"Bearer {settings.MEILISEARCH_KEY}"}
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{settings.MEILISEARCH_URL}/indexes/{INDEX_NAME}/documents/{slug}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code not in (200, 202):
                logger.error(f"Meilisearch delete failed for {slug}: {resp.status_code}")
    except Exception as e:
        logger.error(f"Meilisearch delete error for {slug}: {e}")
