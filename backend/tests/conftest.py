from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.shared.models import Base

# Import all models for metadata
from app.auth.models import ApiKey, User  # noqa: F401
from app.publishers.models import Publisher  # noqa: F401
from app.packages.models import (  # noqa: F401
    Package, PackageVersion, Capability, PackageTag, PackageCategory,
    CompatibilityRule, Dependency, Permission, UpgradeMetadata,
    SecurityFinding, CapabilityTaxonomy,
)
from app.webhooks.models import Webhook, WebhookDelivery  # noqa: F401
from app.admin.models import AdminAuditLog  # noqa: F401
from app.billing.models import ReviewRequest, ProcessedStripeEvent  # noqa: F401
from app.blog.models import BlogPost, BlogImage, BlogCategory, BlogPostType  # noqa: F401

TEST_DATABASE_URL = settings.DATABASE_URL

SEED_CAPABILITY_IDS = [
    ("pdf_extraction", "PDF Extraction", "Extract text and data from PDF documents", "document-processing"),
    ("web_search", "Web Search", "Search the web", "search"),
    ("code_execution", "Code Execution", "Execute code", "developer-tools"),
]


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        # Enable pg_trgm extension (required for typosquatting similarity queries)
        from sqlalchemy import text
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        # Seed capability taxonomy for validation tests
        for cap_id, display_name, description, category in SEED_CAPABILITY_IDS:
            await conn.execute(
                CapabilityTaxonomy.__table__.insert().values(
                    id=cap_id, display_name=display_name,
                    description=description, category=category,
                )
            )
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(session):
    async def override_get_session():
        yield session

    # Mock Redis
    mock_pipe = AsyncMock()
    mock_pipe.execute = AsyncMock(return_value=[0, 0, 1, True])
    mock_pipe.zremrangebyscore = MagicMock(return_value=mock_pipe)
    mock_pipe.zadd = MagicMock(return_value=mock_pipe)
    mock_pipe.zcard = MagicMock(return_value=mock_pipe)
    mock_pipe.expire = MagicMock(return_value=mock_pipe)

    # Simple in-memory store so refresh tokens round-trip correctly
    _redis_store: dict[str, str] = {}

    async def _mock_set(key, value, ex=None, nx=False):
        if nx and key in _redis_store:
            return False  # Key exists — SET NX fails
        _redis_store[key] = value
        return True

    async def _mock_get(key):
        return _redis_store.get(key)

    async def _mock_delete(key):
        _redis_store.pop(key, None)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)
    mock_redis.set = AsyncMock(side_effect=_mock_set)
    mock_redis.get = AsyncMock(side_effect=_mock_get)
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(side_effect=_mock_delete)
    mock_redis.ttl = AsyncMock(return_value=-1)
    mock_redis.close = AsyncMock()
    app.state.redis = mock_redis

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
