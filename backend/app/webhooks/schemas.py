from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

VALID_EVENTS = [
    "package.published",
    "package.deprecated",
    "version.published",
    "version.yanked",
    "version.quarantined",
    "version.cleared",
    "version.rejected",
]


class CreateWebhookRequest(BaseModel):
    url: str = Field(..., min_length=10, max_length=500)
    secret: str | None = Field(None, max_length=200)
    events: list[str] = Field(..., min_length=1)


class WebhookResponse(BaseModel):
    id: UUID
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime


class WebhookDeliveryItem(BaseModel):
    id: UUID
    event_type: str
    status_code: str | None
    success: bool
    delivered_at: datetime
