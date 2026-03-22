"""Reindex all packages to Meilisearch from DB. Admin script.

Usage: python -m scripts.reindex_search [--dry-run]
"""
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Import all models so SQLAlchemy resolves all foreign keys
import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.blog.models  # noqa: F401
import app.verification.models  # noqa: F401

from app.database import async_session_factory
from app.packages.models import Package, PackageVersion
from app.packages.service import build_meili_document
from app.shared.meili import sync_package_to_meilisearch
from app.shared.storage import download_artifact


async def reindex(dry_run: bool = False):
    async with async_session_factory() as session:
        result = await session.execute(
            select(Package).options(
                selectinload(Package.versions),
                selectinload(Package.publisher),
            )
        )
        packages = result.scalars().all()

        count = 0
        errors = 0
        for pkg in packages:
            if pkg.is_deprecated and not pkg.latest_version_id:
                continue

            # Find latest version
            version = None
            if pkg.latest_version_id:
                for v in pkg.versions:
                    if v.id == pkg.latest_version_id:
                        version = v
                        break
            if not version:
                # fallback: newest version
                sorted_versions = sorted(pkg.versions, key=lambda v: v.created_at or "", reverse=True)
                version = sorted_versions[0] if sorted_versions else None

            if not version:
                print(f"  SKIP {pkg.slug}: no version found")
                continue

            # Load manifest from artifact
            manifest = {}
            if version.artifact_object_key:
                try:
                    artifact_bytes = await download_artifact(version.artifact_object_key)
                    if artifact_bytes:
                        import tarfile
                        import io
                        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
                            for member in tar.getmembers():
                                if member.name.endswith("manifest.json"):
                                    f = tar.extractfile(member)
                                    if f:
                                        manifest = json.loads(f.read())
                                    break
                except Exception as e:
                    print(f"  WARN {pkg.slug}: manifest extract failed: {e}")

            doc = build_meili_document(pkg, version, manifest)

            if dry_run:
                print(f"  [{count+1}] {pkg.slug}: score={doc['verification_score']} tier={doc['verification_tier']} status={doc['verification_status']}")
            else:
                try:
                    await sync_package_to_meilisearch(doc)
                    print(f"  [{count+1}] {pkg.slug}: score={doc['verification_score']} tier={doc['verification_tier']} ✓")
                except Exception as e:
                    print(f"  [{count+1}] {pkg.slug}: ERROR {e}")
                    errors += 1

            count += 1

        print(f"\n{'Would reindex' if dry_run else 'Reindexed'} {count} packages ({errors} errors)")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(reindex(dry_run))
