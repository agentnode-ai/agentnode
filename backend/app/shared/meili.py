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


async def sync_package_to_meili(session, package_id) -> None:
    """P1-D1: idempotent upsert/delete of a package in Meili from current DB state.

    Called from: deprecate / undeprecate / yank / unyank / admin overrides.
    Resolves the package's latest non-yanked version and either upserts the
    resulting document into the search index, or — if there are no
    eligible (non-yanked, published) versions — removes the document so
    searches don't surface unreachable rows.

    Never raises. All failures are logged, because search-index drift is
    eventually consistent and must not block the DB-side state change.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.packages.models import Package, PackageVersion
    from app.packages.service import build_meili_document

    try:
        result = await session.execute(
            select(Package)
            .options(selectinload(Package.publisher))
            .where(Package.id == package_id)
        )
        pkg = result.scalar_one_or_none()
        if pkg is None:
            logger.warning("sync_package_to_meili: package %s not found", package_id)
            return

        # Pick the newest non-yanked published version (matches the visible row).
        ver_result = await session.execute(
            select(PackageVersion)
            .where(
                PackageVersion.package_id == pkg.id,
                PackageVersion.is_yanked == False,  # noqa: E712
                PackageVersion.status == "published",
            )
            .order_by(PackageVersion.published_at.desc().nullslast())
            .limit(1)
        )
        pv = ver_result.scalar_one_or_none()

        if pv is None:
            # No visible version → delete from index.
            await delete_package_from_meilisearch(pkg.slug)
            return

        doc = build_meili_document(pkg, pv, pv.manifest_raw or {})
        await sync_package_to_meilisearch(doc)
    except Exception as e:
        logger.error("sync_package_to_meili failed for %s: %s", package_id, e)
