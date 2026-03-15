from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    q: str = ""
    package_type: str | None = None
    capability_id: str | None = None
    framework: str | None = None
    runtime: str | None = None
    trust_level: str | None = None
    sort_by: str | None = None
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1, le=100)


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
    is_deprecated: bool = False


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    total: int
    page: int
    per_page: int
