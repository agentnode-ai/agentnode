"""Set trust level for a publisher. Admin script."""
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.publishers.models import Publisher

VALID_LEVELS = ("unverified", "verified", "trusted", "curated")


async def set_trust(slug: str, level: str):
    if level not in VALID_LEVELS:
        print(f"Invalid level. Must be one of: {VALID_LEVELS}")
        return

    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as session:
        result = await session.execute(select(Publisher).where(Publisher.slug == slug))
        pub = result.scalar_one_or_none()
        if not pub:
            print(f"Publisher '{slug}' not found.")
            return

        pub.trust_level = level
        await session.commit()
        print(f"Publisher '{slug}' trust level set to '{level}'.")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python set_trust_level.py <publisher_slug> <level>")
        sys.exit(1)
    asyncio.run(set_trust(sys.argv[1], sys.argv[2]))
