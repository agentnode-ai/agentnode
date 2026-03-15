"""Suspend or unsuspend a publisher. Admin script."""
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.publishers.models import Publisher


async def suspend(slug: str, reason: str = ""):
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as session:
        result = await session.execute(select(Publisher).where(Publisher.slug == slug))
        pub = result.scalar_one_or_none()
        if not pub:
            print(f"Publisher '{slug}' not found.")
            return

        pub.is_suspended = True
        pub.suspension_reason = reason or "Admin action"
        await session.commit()
        print(f"Publisher '{slug}' suspended.")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python suspend_publisher.py <publisher_slug> [reason]")
        sys.exit(1)
    reason = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
    asyncio.run(suspend(sys.argv[1], reason))
