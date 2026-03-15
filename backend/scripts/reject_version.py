"""Reject a quarantined version. Admin script."""
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


async def reject(package_slug: str, version: str):
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as session:
        from app.packages.models import Package, PackageVersion

        result = await session.execute(
            select(PackageVersion)
            .join(Package)
            .where(Package.slug == package_slug, PackageVersion.version_number == version)
        )
        pv = result.scalar_one_or_none()
        if not pv:
            print(f"Version {package_slug}@{version} not found.")
            return

        pv.quarantine_status = "rejected"
        await session.commit()
        print(f"Version {package_slug}@{version} rejected.")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python reject_version.py <package_slug> <version>")
        sys.exit(1)
    asyncio.run(reject(sys.argv[1], sys.argv[2]))
