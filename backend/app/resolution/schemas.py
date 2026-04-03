from pydantic import BaseModel, Field


class PolicyFilter(BaseModel):
    min_trust: str | None = None
    allow_shell: bool = True
    allow_network: bool = True


class ResolveRequestSchema(BaseModel):
    capabilities: list[str] = Field(..., min_length=1)
    framework: str | None = None
    runtime: str | None = None
    package_type: str | None = None
    limit: int = Field(10, ge=1, le=50)
    policy: PolicyFilter | None = None


class ScoreBreakdown(BaseModel):
    capability: float
    framework: float
    runtime: float
    trust: float
    permissions: float


class ResolvedPackage(BaseModel):
    slug: str
    name: str
    package_type: str
    summary: str
    version: str
    publisher_slug: str
    trust_level: str
    score: float
    policy_result: str = "allowed"
    breakdown: ScoreBreakdown
    matched_capabilities: list[str]
    broad_package: bool = False


class ResolveResponse(BaseModel):
    results: list[ResolvedPackage]
    total: int
