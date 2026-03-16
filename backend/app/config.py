import secrets
import sys

from pydantic_settings import BaseSettings


def _dev_only_default(name: str, fallback: str) -> str:
    """Return fallback for local dev; in production, the env var MUST be set."""
    return fallback


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://agentnode:agentnode@localhost:5432/agentnode"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Meilisearch
    MEILISEARCH_URL: str = "http://localhost:7700"
    MEILISEARCH_KEY: str = "masterKey"

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
