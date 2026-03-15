"""Reindex all packages to Meilisearch from DB. Admin script."""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.config import settings
from app.shared.meili import sync_package_to_meilisearch


async def reindex():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as session:
        from app.packages.models import Package, PackageVersion

        result = await session.execute(
            select(Package).options(
                selectinload(Package.versions),
                selectinload(Package.publisher),
            )
        )
        packages = result.scalars().all()

        count = 0
        for pkg in packages:
            if pkg.is_deprecated and not pkg.latest_version_id:
                continue

            # Build document from latest version
            # This is a simplified version; full implementation comes with packages module
            print(f"Would reindex: {pkg.slug}")
            count += 1

        print(f"Found {count} packages to reindex.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(reindex())
