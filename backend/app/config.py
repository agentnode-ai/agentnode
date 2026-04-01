import secrets
import shutil
import sys

from pydantic_settings import BaseSettings


def _dev_only_default(name: str, fallback: str) -> str:
    """Return fallback for local dev; in production, the env var MUST be set."""
    return fallback


def _detect_system_capabilities() -> dict[str, bool]:
    """Detect available system binaries for verification context."""
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "tesseract": shutil.which("tesseract") is not None,
        "chromium": (
            shutil.which("chromium") is not None
            or shutil.which("chromium-browser") is not None
        ),
        "poppler": shutil.which("pdftotext") is not None,
        "wkhtmltopdf": shutil.which("wkhtmltopdf") is not None,
        "libreoffice": shutil.which("libreoffice") is not None,
    }


def _detect_container_runtime() -> str | None:
    """Detect available container runtime (podman preferred, docker fallback).

    Returns the binary name if a working runtime is found, None otherwise.
    """
    import subprocess
    for runtime in ("podman", "docker"):
        if shutil.which(runtime):
            try:
                result = subprocess.run(
                    [runtime, "info"],
                    capture_output=True, timeout=10,
                )
                if result.returncode == 0:
                    return runtime
            except Exception:
                continue
    return None


SYSTEM_CAPABILITIES = _detect_system_capabilities()
CONTAINER_RUNTIME: str | None = _detect_container_runtime()


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://agentnode:agentnode@localhost:5432/agentnode"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Meilisearch
    MEILISEARCH_URL: str = "http://localhost:7700"
    MEILISEARCH_KEY: str = "masterKey"
    MEILISEARCH_SEARCH_KEY: str = ""

    # S3 / MinIO
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_PUBLIC_ENDPOINT: str = ""
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "agentnode-artifacts"
    S3_REGION: str = "auto"

    # JWT
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # API Keys
    API_KEY_PREFIX: str = "ank_"

    # Cookies
    COOKIE_DOMAIN: str = ""
    COOKIE_SECURE: bool = False  # Set to True in production via env var
    COOKIE_SAMESITE: str = "lax"

    # Login security
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 900  # 15 minutes

    # Email / SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    EMAIL_FROM: str = "noreply@agentnode.net"
    EMAIL_FROM_NAME: str = "AgentNode"
    FRONTEND_URL: str = "https://agentnode.net"

    # AI / Builder
    ANTHROPIC_API_KEY: str = ""

    # Verification pipeline
    VERIFICATION_ENABLED: bool = True
    VERIFICATION_TIMEOUT: int = 240
    VERIFICATION_PIP_TIMEOUT: int = 90
    VERIFICATION_MAX_ARTIFACT_MB: int = 50
    VERIFICATION_MAX_CONCURRENT: int = 2
    VERIFICATION_SMOKE_MAX_TOOLS: int = 5
    VERIFICATION_SMOKE_BUDGET_SECONDS: int = 60
    VERIFICATION_SMOKE_MULTI_RUNS: int = 3

    # Phase 5A: uv installer support (8-85x faster than pip)
    VERIFICATION_USE_UV: bool = True
    VERIFICATION_INSTALL_TIMEOUT: int = 60    # Separate from smoke timeout
    VERIFICATION_SMOKE_TIMEOUT: int = 30      # Shorter smoke timeout

    # Continuous verification (Phase 4C)
    VERIFICATION_REVERIFY_DAYS: int = 30
    VERIFICATION_REVERIFY_BATCH: int = 3
    VERIFICATION_REVERIFY_ENABLED: bool = True

    # Sandbox mode (for environment_info tracking)
    VERIFICATION_SANDBOX_MODE: str = "subprocess"   # "subprocess" or "container"
    VERIFICATION_CONTAINER_IMAGE: str = "agentnode-verifier:latest"

    # Stripe (billing)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_TAX_ENABLED: bool = True
    AGENTNODE_BASE_URL: str = "https://agentnode.net"

    # Publish limits
    MAX_ARTIFACT_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB

    # Environment
    ENVIRONMENT: str = "development"

    model_config = {"env_file": ".env", "extra": "ignore"}

    def _check_production_secrets(self) -> None:
        """Abort startup if production is running with insecure defaults."""
        if self.ENVIRONMENT != "production":
            return
        insecure = []
        if self.JWT_SECRET == "change-me-in-production":
            insecure.append("JWT_SECRET")
        if self.S3_ACCESS_KEY == "minioadmin":
            insecure.append("S3_ACCESS_KEY")
        if self.S3_SECRET_KEY == "minioadmin":
            insecure.append("S3_SECRET_KEY")
        if self.MEILISEARCH_KEY == "masterKey":
            insecure.append("MEILISEARCH_KEY")
        if not self.COOKIE_SECURE:
            insecure.append("COOKIE_SECURE")
        if insecure:
            print(
                f"FATAL: Production environment detected but these settings "
                f"still have insecure defaults: {', '.join(insecure)}. "
                f"Set them via environment variables or .env file.",
                file=sys.stderr,
            )
            sys.exit(1)


settings = Settings()
settings._check_production_secrets()
