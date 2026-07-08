import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Under the test suite (AGENTNODE_TEST_MODE set by conftest before app import),
# use NullPool: fire-and-forget background tasks (verification, security scan)
# from the many publish calls would otherwise exhaust the connection pool across
# the full run, surfacing as AsyncAdaptedQueuePool termination errors and
# spurious 403s. Production keeps the real pool.
if os.environ.get("AGENTNODE_TEST_MODE"):
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        settings.DATABASE_URL, echo=False, poolclass=NullPool
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session
