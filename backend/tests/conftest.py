import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
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

    # ---- In-memory Redis mock with sorted set support ----
    # Shared state: string store + sorted sets (for rate limiter)
    _redis_store: dict[str, str] = {}
    _redis_sorted_sets: dict[str, dict[str, float]] = {}  # key -> {member: score}

    # -- Pipeline mock that queues operations against shared state --
    class _MockPipeline:
        """Simulates a Redis pipeline with sorted set ops for the rate limiter."""

        def __init__(self):
            self._ops: list[tuple] = []

        def zremrangebyscore(self, key, min_score, max_score):
            self._ops.append(("zremrangebyscore", key, min_score, max_score))
            return self

        def zadd(self, key, mapping):
            self._ops.append(("zadd", key, mapping))
            return self

        def zcard(self, key):
            self._ops.append(("zcard", key))
            return self

        def expire(self, key, seconds):
            self._ops.append(("expire", key, seconds))
            return self

        async def execute(self):
            results = []
            for op in self._ops:
                cmd = op[0]
                if cmd == "zremrangebyscore":
                    _, key, min_s, max_s = op
                    ss = _redis_sorted_sets.get(key, {})
                    to_remove = [m for m, s in ss.items() if min_s <= s <= max_s]
                    for m in to_remove:
                        del ss[m]
                    results.append(len(to_remove))
                elif cmd == "zadd":
                    _, key, mapping = op
                    if key not in _redis_sorted_sets:
                        _redis_sorted_sets[key] = {}
                    added = 0
                    for member, score in mapping.items():
                        if member not in _redis_sorted_sets[key]:
                            added += 1
                        _redis_sorted_sets[key][member] = score
                    results.append(added)
                elif cmd == "zcard":
                    _, key = op
                    results.append(len(_redis_sorted_sets.get(key, {})))
                elif cmd == "expire":
                    results.append(True)
            self._ops.clear()
            return results

    # -- String ops (refresh tokens, search cache, etc.) --
    async def _mock_set(key, value, ex=None, nx=False):
        if nx and key in _redis_store:
            return False  # Key exists - SET NX fails
        _redis_store[key] = value
        return True

    async def _mock_get(key):
        return _redis_store.get(key)

    async def _mock_delete(key):
        _redis_store.pop(key, None)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.pipeline = MagicMock(side_effect=lambda: _MockPipeline())
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


# ============================================================
# Shared test helpers
# ============================================================
# These are async helper functions (not pytest fixtures) for use
# in new tests. They reduce the register/login/publish boilerplate
# that is duplicated across many test files.
#
# Existing test files are NOT modified to use these — they are
# provided for new tests going forward.
# ============================================================


# -- Shared test manifest template --
# Callers can override individual keys via dict unpacking:
#   manifest = {**TEST_MANIFEST, "package_id": "my-pkg", "publisher": "my-pub"}

TEST_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "test-pack",
    "package_type": "toolpack",
    "name": "Test Pack",
    "publisher": "test-publisher",
    "version": "1.0.0",
    "summary": "A shared test package for integration testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "test_pack.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
            "input_schema": {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"output": {"type": "string"}},
            },
        }],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"], "python": ">=3.10"},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "temp"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
        "external_integrations": [],
    },
    "tags": ["test"],
    "categories": ["document-processing"],
    "dependencies": [],
    "security": {
        "signature": "",
        "provenance": {"source_repo": "", "commit": "", "build_system": ""},
    },
    "support": {"homepage": "", "issues": ""},
}


async def register_and_login(
    client,
    email: str = "testuser@agentnode.dev",
    username: str = "testuser",
    password: str = "TestPass123!",
) -> str:
    """Register a new user and log in. Returns the access token.

    Usage::

        token = await register_and_login(client)
        token = await register_and_login(client, "alt@test.dev", "altuser", "Pass123!")
    """
    await client.post("/v1/auth/register", json={
        "email": email,
        "username": username,
        "password": password,
    })
    login_resp = await client.post("/v1/auth/login", json={
        "email": email,
        "password": password,
    })
    return login_resp.json()["access_token"]


async def create_publisher(
    client,
    token: str,
    slug: str = "test-publisher",
    display_name: str = "Test Publisher",
) -> dict:
    """Create a publisher profile for the authenticated user. Returns the response JSON.

    Usage::

        pub = await create_publisher(client, token)
        pub = await create_publisher(client, token, "my-pub", "My Publisher")
    """
    resp = await client.post(
        "/v1/publishers",
        json={"slug": slug, "display_name": display_name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, f"Failed to create publisher: {resp.text}"
    return resp.json()


async def setup_publisher_user(
    client,
    email: str = "testuser@agentnode.dev",
    username: str = "testuser",
    password: str = "TestPass123!",
    pub_slug: str = "test-publisher",
    pub_name: str = "Test Publisher",
) -> tuple[str, dict]:
    """Full setup: register user, log in, create publisher. Returns (token, publisher_data).

    Usage::

        token, pub = await setup_publisher_user(client)
        token, pub = await setup_publisher_user(
            client, "dev@test.dev", "devuser", "Pass123!", "dev-pub", "Dev Publisher"
        )
    """
    token = await register_and_login(client, email, username, password)
    pub_data = await create_publisher(client, token, pub_slug, pub_name)
    return token, pub_data


async def setup_admin_user(
    client,
    session,
    email: str = "admin@agentnode.dev",
    username: str = "adminuser",
    password: str = "AdminPass123!",
    pub_slug: str = "admin-publisher",
    pub_name: str = "Admin Publisher",
) -> tuple[str, dict]:
    """Full setup for an admin: register, login, create publisher, promote to admin.
    Returns (token, publisher_data).

    Usage::

        admin_token, pub = await setup_admin_user(client, session)
    """
    token, pub_data = await setup_publisher_user(
        client, email, username, password, pub_slug, pub_name,
    )
    await session.execute(
        update(User).where(User.username == username).values(is_admin=True)
    )
    await session.commit()
    return token, pub_data


async def publish_test_package(
    client,
    token: str,
    manifest: dict | None = None,
) -> dict:
    """Publish a package using the provided manifest (or the shared TEST_MANIFEST).
    Returns the response JSON.

    The manifest's ``publisher`` field must match an existing publisher owned
    by the authenticated user.

    Usage::

        data = await publish_test_package(client, token)
        data = await publish_test_package(client, token, {**TEST_MANIFEST, "version": "2.0.0"})
    """
    if manifest is None:
        manifest = TEST_MANIFEST
    resp = await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(manifest)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Failed to publish package: {resp.text}"
    return resp.json()
