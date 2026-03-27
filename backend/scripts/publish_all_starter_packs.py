#!/usr/bin/env python3
"""Publish all starter packs with real artifacts to the database.

Re-publishes over existing entries (new version with real artifact).
Does NOT delete old entries.

Usage:
    cd backend
    python -m scripts.publish_all_starter_packs [--dry-run] [--single SLUG]

Requires:
    - Build artifacts in build/artifacts/{slug}.tar.gz (run build_starter_artifacts.py first)
    - Running database (reads DATABASE_URL from .env)
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

import yaml

# Adjust path for backend imports
BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
ARTIFACTS_DIR = ROOT_DIR / "build" / "artifacts"
STARTER_PACKS_DIR = ROOT_DIR / "starter-packs"

sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")


async def publish_one(slug: str, dry_run: bool = False) -> bool:
    """Publish a single starter pack."""
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.packages.service import publish_package
    from app.publishers.models import Publisher

    pack_dir = STARTER_PACKS_DIR / slug
    artifact_path = ARTIFACTS_DIR / f"{slug}.tar.gz"

    # Read manifest
    manifest_path = pack_dir / "agentnode.yaml"
    if not manifest_path.exists():
        print(f"  SKIP {slug}: no agentnode.yaml")
        return False

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    # Read artifact
    if not artifact_path.exists():
        print(f"  SKIP {slug}: no artifact at {artifact_path}")
        return False

    artifact_bytes = artifact_path.read_bytes()

    if dry_run:
        size_kb = len(artifact_bytes) / 1024
        print(f"  DRY-RUN {slug}: manifest OK, artifact {size_kb:.1f} KB")
        return True

    async with async_session_factory() as session:
        # Get agentnode publisher (curated publisher, skips quarantine)
        result = await session.execute(
            select(Publisher).where(Publisher.slug == "agentnode")
        )
        publisher = result.scalar_one_or_none()
        if not publisher:
            print(f"  ERROR: 'agentnode' publisher not found in database")
            return False

        try:
            # Monkey-patch email sending to no-op during batch publish
            import app.shared.email as email_module
            _orig_send = email_module.send_package_published_email
            email_module.send_package_published_email = asyncio.coroutine(lambda *a, **kw: None)

            try:
                pkg, version, warnings = await publish_package(
                    manifest=manifest,
                    publisher_id=publisher.id,
                    session=session,
                    artifact_bytes=artifact_bytes,
                )
            finally:
                # Restore original email function
                email_module.send_package_published_email = _orig_send

            print(f"  PUBLISHED {slug} v{version.version_number} (artifact: {version.artifact_object_key})")
            if warnings:
                for w in warnings:
                    print(f"    WARN: {w}")
            return True
        except Exception as e:
            await session.rollback()
            print(f"  ERROR {slug}: {e}")
            return False


async def main():
    parser = argparse.ArgumentParser(description="Publish all starter packs")
    parser.add_argument("--dry-run", action="store_true", help="Validate without publishing")
    parser.add_argument("--single", type=str, help="Publish a single pack by slug")
    parser.add_argument("--sleep", type=float, default=0.1, help="Sleep between publishes (seconds)")
    args = parser.parse_args()

    if args.single:
        slugs = [args.single]
    else:
        slugs = sorted(
            d.name for d in STARTER_PACKS_DIR.iterdir()
            if d.is_dir() and (d / "agentnode.yaml").exists()
        )

    print(f"Publishing {len(slugs)} starter packs {'(DRY RUN)' if args.dry_run else ''}")
    print("=" * 60)

    published = 0
    failed = 0

    for slug in slugs:
        ok = await publish_one(slug, dry_run=args.dry_run)
        if ok:
            published += 1
        else:
            failed += 1

        if not args.dry_run and args.sleep > 0:
            time.sleep(args.sleep)  # Rate-limit for Meilisearch

    print("=" * 60)
    print(f"Done: {published} published, {failed} failed, {len(slugs)} total")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
