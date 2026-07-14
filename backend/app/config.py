import shutil
import sys

from pydantic import Field
from pydantic_settings import BaseSettings


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
                    capture_output=True,
                    timeout=10,
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
    DATABASE_URL: str = (
        "postgresql+asyncpg://agentnode:agentnode@localhost:5432/agentnode"
    )

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

    # AI / Builder — OpenRouter is preferred when configured (cheap models,
    # one key shared with the compatibility pipeline); Anthropic-direct stays
    # as fallback; without any key the builder uses the heuristic generator.
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    BUILDER_MODEL: str = "google/gemini-2.5-flash-lite"

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
    VERIFICATION_INSTALL_TIMEOUT: int = 60  # Separate from smoke timeout
    VERIFICATION_SMOKE_TIMEOUT: int = 30  # Shorter smoke timeout

    # Continuous verification (Phase 4C)
    VERIFICATION_REVERIFY_DAYS: int = 30
    VERIFICATION_REVERIFY_BATCH: int = 3
    VERIFICATION_REVERIFY_ENABLED: bool = True

    # Sandbox mode (for environment_info tracking)
    VERIFICATION_SANDBOX_MODE: str = "subprocess"  # "subprocess" or "container"
    VERIFICATION_CONTAINER_IMAGE: str = "agentnode-verifier:latest"
    VERIFICATION_CONTAINER_IMAGE_BROWSER: str = "agentnode-verifier-browser:latest"
    VERIFICATION_MODEL_CACHE_DIR: str = "/opt/agentnode/model-cache"
    VERIFICATION_SMOKE_BUDGET_SECONDS_HEAVY: int = 180

    # MCP sandbox smoke (Slice 2c) — INERT by default. When MCP_SMOKE_MODE !=
    # "container" the executor reports 'unavailable' (fail-closed) and never runs
    # a container. Enabling it in prod is a separate gated config + deploy. The
    # image is the pinned multi-runtime sandbox (node/npx + python/uvx), distinct
    # from the python-only verifier image.
    MCP_SMOKE_MODE: str = "disabled"  # "disabled" or "container"
    MCP_SMOKE_IMAGE: str = (
        "ghcr.io/agentnode-ai/sandbox@sha256:"
        "6c77561965dc9e98ed9cd0437c4de9aa9171cd3753ae9f11672450ce3125c80f"
    )
    MCP_SMOKE_MAX_CONCURRENT: int = 1
    MCP_SMOKE_INSTALL_TIMEOUT: int = 120
    MCP_SMOKE_RUNTIME_TIMEOUT: int = 30
    # 2c-4a: a passed smoke stays valid for this long (transitive-dep drift),
    # bound to the SmokeResult's binding keys. Bump MCP_SMOKE_SCHEMA_VERSION on a
    # security-relevant executor change to invalidate all older passed smokes.
    MCP_SMOKE_TTL_DAYS: int = 30
    MCP_SMOKE_SCHEMA_VERSION: int = 1
    # G3 — host-resource preflight + backlog/output bounding (safe permanent-
    # activation prerequisites). Conservative defaults for the 2-core, no-swap host.
    # None of these ACTIVATES a smoke; MCP_SMOKE_MODE stays "disabled" by default.
    # Never start a container below these host thresholds (measured just before the
    # docker run); a shortfall is transient/review, never a hard package fault.
    MCP_SMOKE_MIN_AVAILABLE_MEMORY_MB: int = Field(1024, ge=1)
    MCP_SMOKE_MIN_FREE_DISK_GB: int = Field(5, ge=1)
    # active runs are capped by the semaphore (MCP_SMOKE_MAX_CONCURRENT); this caps
    # how many MORE tasks may WAIT. max in-flight = 1 active + MCP_SMOKE_MAX_PENDING.
    MCP_SMOKE_MAX_PENDING: int = Field(1, ge=0)
    # hard cap on runtime-container stdout buffered in the API process (bounds the
    # reader against a chatty server or a single giant line).
    MCP_SMOKE_MAX_OUTPUT_BYTES: int = Field(1_048_576, ge=1024)
    # Docker data lives here; on this host it is on / (same fs), so the disk
    # preflight statvfs's this path. Kept configurable for non-standard hosts.
    MCP_SMOKE_DOCKER_ROOT: str = "/var/lib/docker"

    # Stripe (billing)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_TAX_ENABLED: bool = True
    AGENTNODE_BASE_URL: str = "https://agentnode.net"

    # Credential encryption (Fernet key — generate with Fernet.generate_key())
    CREDENTIAL_ENCRYPTION_KEY: str = ""

    # Publish limits
    MAX_ARTIFACT_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB

    # Registry signing (TG-4)
    REGISTRY_SIGNING_KEY: str = ""  # base64-encoded PEM Ed25519 private key
    REGISTRY_SIGNING_KEY_ID: str = ""  # e.g. "registry-2026"

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
        if not self.REGISTRY_SIGNING_KEY:
            print(
                "WARNING: REGISTRY_SIGNING_KEY not set — "
                "trust-critical responses will not be signed",
                file=sys.stderr,
            )


settings = Settings()
settings._check_production_secrets()


def check_verification_sandbox() -> None:
    """Abort startup if container sandbox mode is configured but unavailable.

    This prevents silent verification failures that get blamed on publishers
    when the real problem is a missing container image or runtime.
    """
    if settings.VERIFICATION_SANDBOX_MODE != "container":
        return
    if not CONTAINER_RUNTIME:
        print(
            "FATAL: VERIFICATION_SANDBOX_MODE=container but no container runtime "
            "(docker/podman) found. Either install a container runtime, or set "
            "VERIFICATION_SANDBOX_MODE=subprocess.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Check if the image exists
    import subprocess as _sp

    try:
        result = _sp.run(
            [
                CONTAINER_RUNTIME,
                "image",
                "inspect",
                settings.VERIFICATION_CONTAINER_IMAGE,
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(
                f"FATAL: VERIFICATION_SANDBOX_MODE=container but image "
                f"'{settings.VERIFICATION_CONTAINER_IMAGE}' not found. "
                f"Build the image or set VERIFICATION_SANDBOX_MODE=subprocess.",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as e:
        print(
            f"FATAL: Cannot verify container image availability: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


check_verification_sandbox()
