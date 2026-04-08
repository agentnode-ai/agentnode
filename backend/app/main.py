from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.router import router as admin_router
from app.billing.router import router as billing_router
from app.blog.router import admin_router as blog_admin_router, public_router as blog_public_router
from app.invites.router import router as invites_router, admin_router as invites_admin_router
from app.sitemap.router import router as sitemap_router, admin_router as sitemap_admin_router
from app.builder.router import router as builder_router
from app.import_.router import router as import_router
from app.auth.router import router as auth_router
from app.config import settings
from app.database import engine
from app.install.router import installations_router, router as install_router
from app.packages.router import router as packages_router
from app.publishers.router import router as publishers_router
from app.resolution.router import router as resolution_router
from app.search.router import router as search_router
from app.shared.exceptions import AppError, app_error_handler
from app.shared.logging_middleware import RequestLoggingMiddleware
from app.trust.router import router as trust_router
from app.verification.router import router as verification_router
from app.compatibility.router import router as compatibility_router
from app.webhooks.router import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create Redis connection pool
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    # Startup: create shared httpx clients
    from app.shared.meili import init_meili_client, close_meili_client
    from app.webhooks.service import init_webhook_client, close_webhook_client
    init_meili_client()
    init_webhook_client()

    # Start background cron tasks
    from app.tasks.cron import start_cron_tasks, stop_cron_tasks
    start_cron_tasks()

    # Load API keys from database into settings
    try:
        from sqlalchemy import select as sa_select
        from app.admin.models import SystemSetting
        async with AsyncSession(engine) as session:
            result = await session.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "api_keys")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                if row.value.get("anthropic_api_key"):
                    settings.ANTHROPIC_API_KEY = row.value["anthropic_api_key"]
    except Exception:
        pass  # DB may not have the table yet

    # Load SMTP config into memory cache (avoids per-email DB queries)
    try:
        from app.shared.email import load_smtp_config
        async with AsyncSession(engine) as session:
            await load_smtp_config(session)
    except Exception:
        pass  # Will fall back to env vars

    yield

    # Shutdown
    stop_cron_tasks()
    # Close search httpx client
    from app.search.router import _search_client
    if _search_client is not None:
        await _search_client.aclose()
    # Close shared httpx clients
    await close_meili_client()
    await close_webhook_client()
    await app.state.redis.close()
    await engine.dispose()


app = FastAPI(title="AgentNode API", version="0.1.0", lifespan=lifespan)

# CORS
allowed_origins = [
    "https://agentnode.net",
    "https://www.agentnode.net",
]
if settings.ENVIRONMENT == "development":
    allowed_origins.append("http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

# Error handlers
app.add_exception_handler(AppError, app_error_handler)

# Routers
app.add_middleware(RequestLoggingMiddleware)

app.include_router(auth_router)
app.include_router(publishers_router)
app.include_router(packages_router)
app.include_router(install_router)
app.include_router(installations_router)
app.include_router(resolution_router)
app.include_router(search_router)
app.include_router(admin_router)
app.include_router(trust_router)
app.include_router(verification_router)
app.include_router(builder_router)
app.include_router(import_router)
app.include_router(billing_router)
app.include_router(webhooks_router)
app.include_router(blog_admin_router)
app.include_router(blog_public_router)
app.include_router(sitemap_router)
app.include_router(sitemap_admin_router)
app.include_router(invites_router)
app.include_router(invites_admin_router)
app.include_router(compatibility_router)


@app.get("/health")
@app.get("/healthz")
@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    details = {}
    ready = True

    # Check PostgreSQL
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        details["postgres"] = "ok"
    except Exception:
        details["postgres"] = "unavailable"
        ready = False

    # Check Redis
    try:
        await app.state.redis.ping()
        details["redis"] = "ok"
    except Exception:
        details["redis"] = "unavailable"
        ready = False

    # Check Meilisearch (optional in MVP)
    try:
        from app.shared.meili import get_meili_client
        client = get_meili_client()
        resp = await client.get("/health")
        details["meilisearch"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        details["meilisearch"] = "unavailable (non-critical)"

    if not ready:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503, content={"status": "not_ready", "details": details}
        )

    return {"status": "ready", "details": details}
