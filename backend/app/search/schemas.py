from pydantic import BaseModel, Field, field_validator

from app.shared.validators import is_safe_filter_value

# Allowed sort fields to prevent sort injection
_ALLOWED_SORTS = {
    "download_count:asc",
    "download_count:desc",
    "install_count:asc",
    "install_count:desc",
    "published_at:asc",
    "published_at:desc",
    "name:asc",
    "name:desc",
}


_ALLOWED_FACETS = frozenset(
    {
        "package_type",
        "runtime",
        "frameworks",
        "trust_level",
        "verification_tier",
        "tags",
    }
)


class SearchRequest(BaseModel):
    q: str = Field("", max_length=256)
    package_type: str | None = None
    capability_id: str | None = None
    framework: str | None = None
    runtime: str | None = None
    trust_level: str | None = None
    verification_tier: str | None = None
    tag: str | None = None
    publisher_slug: str | None = None
    sort_by: str | None = None
    page: int = Field(1, ge=1, le=500)
    per_page: int = Field(20, ge=1, le=100)
    # Facet distributions to return alongside hits (whitelisted attribute
    # names). Lets the UI build data-driven filter sidebars: only values
    # that actually exist in the current scope are offered.
    facets: list[str] | None = Field(None, max_length=6)

    @field_validator("facets", mode="before")
    @classmethod
    def sanitize_facets(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if not isinstance(v, list) or any(f not in _ALLOWED_FACETS for f in v):
            raise ValueError("Invalid facet name")
        return v

    @field_validator(
        "package_type",
        "capability_id",
        "framework",
        "runtime",
        "trust_level",
        "verification_tier",
        "tag",
        "publisher_slug",
        mode="before",
    )
    @classmethod
    def sanitize_filter_value(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not is_safe_filter_value(v):
            raise ValueError("Invalid filter value")
        return v

    @field_validator("sort_by", mode="before")
    @classmethod
    def sanitize_sort_by(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in _ALLOWED_SORTS:
            raise ValueError("Invalid sort_by value")
        return v


class SearchHit(BaseModel):
    slug: str
    name: str
    package_type: str
    summary: str
    publisher_name: str
    publisher_slug: str
    trust_level: str
    latest_version: str | None = None
    runtime: str | None = None
    capability_ids: list[str] = []
    tags: list[str] = []
    frameworks: list[str] = []
    download_count: int = 0
    install_count: int = 0
    is_deprecated: bool = False
    verification_status: str | None = None
    verification_score: int | None = None
    verification_tier: str | None = None
    network_level: str | None = None
    filesystem_level: str | None = None
    code_execution_level: str | None = None
    has_connector: bool | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    total: int
    page: int
    per_page: int
    # Present only when the request asked for facets:
    # {"tags": {"database": 2, ...}, "runtime": {"python": 83, ...}}
    facet_distribution: dict[str, dict[str, int]] | None = None
