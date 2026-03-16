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
