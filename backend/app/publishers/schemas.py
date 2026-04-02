import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class CreatePublisherRequest(BaseModel):
    display_name: str
    slug: str
    bio: str | None = None
    website_url: str | None = None
    github_url: str | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]{3,40}$", v):
            raise ValueError("Slug must be 3-40 chars, only lowercase letters, digits, hyphens")
        return v


class UpdatePublisherRequest(BaseModel):
    display_name: str | None = None
    bio: str | None = None
    website_url: str | None = None
    github_url: str | None = None


class PublisherResponse(BaseModel):
    id: UUID
    display_name: str
    slug: str
    bio: str | None
    trust_level: str
    website_url: str | None
    github_url: str | None
    packages_published_count: int
    created_at: datetime


class RegisterSigningKeyRequest(BaseModel):
    public_key: str

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, v: str) -> str:
        import base64
        try:
            key_bytes = base64.b64decode(v, validate=True)
        except Exception:
            raise ValueError("public_key must be valid base64")
        if len(key_bytes) != 32:
            raise ValueError("public_key must be a 32-byte Ed25519 public key")
        return v


class SigningKeyResponse(BaseModel):
    public_key: str
    registered_at: datetime
