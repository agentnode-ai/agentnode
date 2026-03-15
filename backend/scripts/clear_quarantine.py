"""Clear quarantine for a package version. Admin script."""
import asyncio
import sys

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


async def clear_quarantine(package_slug: str, version: str):
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as session:
        # Import here to avoid circular imports at module level
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

        pv.quarantine_status = "cleared"
        await session.commit()
        print(f"Quarantine cleared for {package_slug}@{version}.")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python clear_quarantine.py <package_slug> <version>")
        sys.exit(1)
    asyncio.run(clear_quarantine(sys.argv[1], sys.argv[2]))
