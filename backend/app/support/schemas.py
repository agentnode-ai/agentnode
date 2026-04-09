from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateTicketBody(BaseModel):
    category: str = Field(..., pattern=r"^(account|publishing|reviews|billing|bug|other)$")
    subject: str = Field(..., min_length=5, max_length=200)
    message: str = Field(..., min_length=10, max_length=5000)


class ReplyBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


class AdminSetStatusBody(BaseModel):
    status: str = Field(..., pattern=r"^(open|in_progress|resolved|closed)$")


class MessageResponse(BaseModel):
    id: UUID
    is_admin: bool
    body: str
    created_at: datetime
    author_name: str | None


class TicketResponse(BaseModel):
    id: UUID
    ticket_number: int
    category: str
    subject: str
    status: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    messages: list[MessageResponse]


class TicketListItem(BaseModel):
    id: UUID
    ticket_number: int
    category: str
    subject: str
    status: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    last_reply_is_admin: bool
    username: str | None = None
