"""Reverify a single package by slug."""
import asyncio
import sys
from sqlalchemy import select, text

import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.blog.models  # noqa: F401
import app.verification.models  # noqa: F401

from app.database import async_session_factory
from app.packages.models import Package, PackageVersion
from app.verification.pipeline import run_verification


async def main(slug):
    async with async_session_factory() as session:
        r = await session.execute(
            select(PackageVersion)
            .where(
                PackageVersion.package_id == select(Package.id).where(Package.slug == slug).scalar_subquery()
            )
            .order_by(PackageVersion.published_at.desc())
            .limit(1)
        )
        pv = r.scalar_one_or_none()
        if not pv:
            print(f"Package {slug} not found")
            return
        pv_id = pv.id
        print(f"Reverifying {slug} (version_id={pv_id})...")

    await run_verification(pv_id, "admin_reverify")
    print("Done!")

    async with async_session_factory() as session:
        r = await session.execute(
            text(
                "SELECT DISTINCT ON (vr.package_version_id)"
                " vr.tests_status, vr.tests_auto_generated, vr.verification_score,"
                " vr.verification_tier, vr.tests_log, vr.smoke_status, vr.smoke_reason"
                " FROM verification_results vr"
                " JOIN package_versions pv ON vr.package_version_id = pv.id"
                " JOIN packages p ON pv.package_id = p.id"
                " WHERE p.slug = :slug"
                " ORDER BY vr.package_version_id, vr.created_at DESC"
            ),
            {"slug": slug},
        )
        row = r.fetchone()
        print(f"  tests={row[0]} auto={row[1]} score={row[2]} tier={row[3]}")
        print(f"  smoke={row[5]} reason={row[6]}")
        log = row[4] or ""
        print(f"  test_log (last 500): {log[-500:]}")


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "word-counter-pack"
    asyncio.run(main(slug))
