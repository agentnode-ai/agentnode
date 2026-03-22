"""Reverify ALL packages to collect multi-run quality metrics.

Usage: python -m scripts.reverify_all [--dry-run] [--delay SECONDS]
"""
import asyncio
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
from app.verification.pipeline import run_verification


async def reverify_all(dry_run: bool = False, delay: float = 10.0):
    async with async_session_factory() as session:
        result = await session.execute(
            select(PackageVersion)
            .join(Package, Package.id == PackageVersion.package_id)
            .where(
                PackageVersion.artifact_object_key.isnot(None),
                PackageVersion.verification_status != "running",
            )
            .options(selectinload(PackageVersion.package))
            .order_by(Package.slug)
        )
        versions = result.scalars().all()
        print(f"Found {len(versions)} package versions to reverify")

        for i, pv in enumerate(versions, 1):
            slug = pv.package.slug if pv.package else "unknown"
            print(f"  [{i}/{len(versions)}] {slug} v{pv.version_number}", flush=True)

            if not dry_run:
                try:
                    await run_verification(pv.id, triggered_by="admin_reverify")
                    print(f"    ✓ done", flush=True)
                except Exception as e:
                    print(f"    ✗ error: {e}", flush=True)

                if i < len(versions):
                    print(f"    waiting {delay}s...", flush=True)
                    await asyncio.sleep(delay)

    print(f"\n{'Would reverify' if dry_run else 'Reverified'} {len(versions)} packages")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    delay = 10.0
    if "--delay" in sys.argv:
        idx = sys.argv.index("--delay")
        if idx + 1 < len(sys.argv):
            delay = float(sys.argv[idx + 1])
    asyncio.run(reverify_all(dry_run, delay))
