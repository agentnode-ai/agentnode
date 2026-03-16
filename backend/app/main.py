from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.admin.router import router as admin_router
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
from app.webhooks.router import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create Redis connection pool
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    yield
    # Shutdown
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
app.include_router(webhooks_router)


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
    except Exception as e:
        details["postgres"] = str(e)
        ready = False

    # Check Redis
    try:
        await app.state.redis.ping()
        details["redis"] = "ok"
    except Exception as e:
        details["redis"] = str(e)
        ready = False

    # Check Meilisearch (optional in MVP)
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.MEILISEARCH_URL}/health", timeout=5)
            details["meilisearch"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        details["meilisearch"] = "unavailable (non-critical)"

    if not ready:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503, content={"status": "not_ready", "details": details}
        )

    return {"status": "ready", "details": details}
