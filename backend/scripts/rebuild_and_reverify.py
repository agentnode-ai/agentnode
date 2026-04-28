"""Rebuild artifacts from local starter-packs and reverify.

Replaces the S3 artifact with a freshly built tar.gz from the local
starter-packs directory, then triggers reverification. Use this after
updating tests or code in starter-packs without bumping versions.

Usage:
    python -m scripts.rebuild_and_reverify [--dry-run] [--filter SLUG_PATTERN]
    python -m scripts.rebuild_and_reverify --filter "*-pack"
    python -m scripts.rebuild_and_reverify --filter "csv-analyzer-pack"
"""
import asyncio
import fnmatch
import io
import os
import sys
import tarfile

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.blog.models  # noqa: F401
import app.verification.models  # noqa: F401

from app.database import async_session_factory
from app.packages.models import Package, PackageVersion
from app.shared.storage import upload_artifact
from app.verification.pipeline import run_verification

STARTER_PACKS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "starter-packs")

EXCLUDE_PATTERNS = {
    "__pycache__", "*.pyc", ".git", "node_modules", ".venv", "venv",
    "env", "dist", "build", "*.egg-info", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".DS_Store", ".env", ".env.local",
}


def _should_exclude(name: str) -> bool:
    basename = os.path.basename(name)
    for pattern in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(basename, pattern):
            return True
    return False


def build_artifact(pack_dir: str) -> bytes:
    buf = io.BytesIO()
    pack_name = os.path.basename(pack_dir)
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(pack_dir):
            dirs[:] = [d for d in dirs if not _should_exclude(d)]
            for f in files:
                if _should_exclude(f):
                    continue
                full_path = os.path.join(root, f)
                arcname = os.path.join(pack_name, os.path.relpath(full_path, pack_dir))
                tar.add(full_path, arcname=arcname)
    return buf.getvalue()


async def rebuild_and_reverify(dry_run: bool = False, slug_filter: str = "*"):
    async with async_session_factory() as session:
        result = await session.execute(
            select(PackageVersion)
            .join(Package, Package.id == PackageVersion.package_id)
            .where(PackageVersion.artifact_object_key.isnot(None))
            .options(selectinload(PackageVersion.package))
            .order_by(Package.slug)
        )
        versions = result.scalars().all()

    seen_slugs = set()
    targets = []
    for pv in versions:
        slug = pv.package.slug if pv.package else "unknown"
        if slug in seen_slugs:
            continue
        if not fnmatch.fnmatch(slug, slug_filter):
            continue
        pack_dir = os.path.join(STARTER_PACKS_DIR, slug)
        if not os.path.isdir(pack_dir):
            continue
        seen_slugs.add(slug)
        targets.append((slug, pv, pack_dir))

    print(f"Found {len(targets)} packages to rebuild and reverify")

    success = 0
    errors = 0
    for i, (slug, pv, pack_dir) in enumerate(targets, 1):
        print(f"  [{i}/{len(targets)}] {slug} v{pv.version_number}", flush=True)

        if dry_run:
            artifact = build_artifact(pack_dir)
            print(f"    artifact: {len(artifact)} bytes (dry-run, not uploading)")
            continue

        try:
            artifact = build_artifact(pack_dir)
            print(f"    artifact: {len(artifact)} bytes, uploading...", flush=True)
            await upload_artifact(pv.artifact_object_key, artifact)

            print(f"    reverifying...", flush=True)
            await run_verification(pv.id, triggered_by="admin_reverify")

            async with async_session_factory() as session:
                from sqlalchemy import text
                row = await session.execute(
                    text(
                        "SELECT vr.verification_score, vr.verification_tier, vr.tests_status, vr.tests_execution_mode"
                        " FROM verification_results vr"
                        " WHERE vr.package_version_id = :pv_id"
                        " ORDER BY vr.created_at DESC LIMIT 1"
                    ),
                    {"pv_id": str(pv.id)},
                )
                r = row.fetchone()
                if r:
                    print(f"    score={r[0]} tier={r[1]} tests={r[2]} mode={r[3]}")
                else:
                    print(f"    (no verification result)")

            success += 1
        except Exception as e:
            print(f"    ERROR: {e}", flush=True)
            errors += 1

        if i < len(targets):
            await asyncio.sleep(2)

    print(f"\nDone: {success} success, {errors} errors")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    slug_filter = "*"
    if "--filter" in sys.argv:
        idx = sys.argv.index("--filter")
        if idx + 1 < len(sys.argv):
            slug_filter = sys.argv[idx + 1]
    asyncio.run(rebuild_and_reverify(dry_run, slug_filter))
