from datetime import datetime

from pydantic import BaseModel, Field


# --- Quarantine ---

class QuarantineVersionRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class QuarantineActionResponse(BaseModel):
    slug: str
    version: str
    quarantine_status: str
    message: str


# --- Trust ---

class SetTrustLevelRequest(BaseModel):
    trust_level: str = Field(..., pattern="^(unverified|verified|trusted|curated)$")


class TrustLevelResponse(BaseModel):
    publisher_slug: str
    trust_level: str
    message: str


# --- Suspension ---

class SuspendPublisherRequest(BaseModel):
    reason: str = Field("Admin action", max_length=500)


class SuspensionResponse(BaseModel):
    publisher_slug: str
    is_suspended: bool
    suspension_reason: str | None
    message: str


# --- Listing ---

class QuarantinedVersionItem(BaseModel):
    package_slug: str
    version_number: str
    quarantine_status: str
    quarantined_at: datetime | None
    quarantine_reason: str | None


class SuspendedPublisherItem(BaseModel):
    slug: str
    display_name: str
    is_suspended: bool
    suspension_reason: str | None
    trust_level: str
