from pydantic_settings import BaseSettings


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


settings = Settings()
