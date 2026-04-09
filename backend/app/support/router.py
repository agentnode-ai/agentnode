import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.auth.models import User
from app.database import get_session
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit
from app.support.models import SupportMessage, SupportTicket
from app.support.schemas import (
    AdminSetStatusBody,
    CreateTicketBody,
    MessageResponse,
    ReplyBody,
    TicketListItem,
    TicketResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["support"])


# ---- Helpers ----


async def _build_messages(session: AsyncSession, ticket_id: UUID) -> list[MessageResponse]:
    result = await session.execute(
        select(SupportMessage)
        .where(SupportMessage.ticket_id == ticket_id)
        .order_by(SupportMessage.created_at.asc())
    )
    messages = result.scalars().all()

    # Batch-load usernames
    user_ids = {m.user_id for m in messages if m.user_id is not None}
    username_map: dict[UUID, str] = {}
    if user_ids:
        user_result = await session.execute(
            select(User.id, User.username).where(User.id.in_(user_ids))
        )
        for row in user_result.all():
            username_map[row.id] = row.username

    return [
        MessageResponse(
            id=m.id,
            is_admin=m.is_admin,
            body=m.body,
            created_at=m.created_at,
            author_name="Support Team" if m.is_admin else username_map.get(m.user_id),
        )
        for m in messages
    ]


async def _get_user_ticket(session: AsyncSession, ticket_id: UUID, user_id: UUID) -> SupportTicket:
    result = await session.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.user_id == user_id,
        )
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise AppError("TICKET_NOT_FOUND", "Support ticket not found", 404)
    return ticket


async def _get_any_ticket(session: AsyncSession, ticket_id: UUID) -> SupportTicket:
    result = await session.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise AppError("TICKET_NOT_FOUND", "Support ticket not found", 404)
    return ticket


def _ticket_list_item(
    ticket: SupportTicket, message_count: int, last_reply_is_admin: bool, username: str | None = None,
) -> TicketListItem:
    return TicketListItem(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        category=ticket.category,
        subject=ticket.subject,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        message_count=message_count,
        last_reply_is_admin=last_reply_is_admin,
        username=username,
    )


# ---- User Endpoints ----


@router.post(
    "/v1/support/tickets",
    response_model=TicketResponse,
    dependencies=[Depends(rate_limit(5, 3600))],
)
async def create_ticket(
    body: CreateTicketBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create a new support ticket with the initial message."""
    ticket = SupportTicket(
        user_id=user.id,
        category=body.category,
        subject=body.subject,
        status="open",
    )
    session.add(ticket)
    await session.flush()

    message = SupportMessage(
        ticket_id=ticket.id,
        user_id=user.id,
        is_admin=False,
        body=body.message,
    )
    session.add(message)
    await session.commit()

    # Refresh to get DB-generated values
    await session.refresh(ticket)

    # Send admin notification in background
    background_tasks.add_task(
        _notify_admins_new_ticket, ticket.ticket_number, body.subject,
        str(ticket.id), body.category, user.username,
    )

    messages = await _build_messages(session, ticket.id)
    return TicketResponse(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        category=ticket.category,
        subject=ticket.subject,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        messages=messages,
    )


@router.get(
    "/v1/support/tickets",
    response_model=list[TicketListItem],
    dependencies=[Depends(rate_limit(30, 60))],
)
async def list_my_tickets(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List the current user's support tickets."""
    result = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user.id)
        .order_by(SupportTicket.updated_at.desc())
        .limit(100)
    )
    tickets = result.scalars().all()
    if not tickets:
        return []

    ticket_ids = [t.id for t in tickets]

    # Batch message counts
    count_result = await session.execute(
        select(SupportMessage.ticket_id, func.count(SupportMessage.id))
        .where(SupportMessage.ticket_id.in_(ticket_ids))
        .group_by(SupportMessage.ticket_id)
    )
    count_map = {row[0]: row[1] for row in count_result.all()}

    # Batch last message is_admin
    last_msg_result = await session.execute(
        text("""
            SELECT DISTINCT ON (ticket_id) ticket_id, is_admin
            FROM support_messages
            WHERE ticket_id = ANY(:ids)
            ORDER BY ticket_id, created_at DESC
        """),
        {"ids": ticket_ids},
    )
    last_admin_map = {row[0]: row[1] for row in last_msg_result.all()}

    return [
        _ticket_list_item(t, count_map.get(t.id, 0), last_admin_map.get(t.id, False))
        for t in tickets
    ]


@router.get(
    "/v1/support/tickets/{ticket_id}",
    response_model=TicketResponse,
    dependencies=[Depends(rate_limit(30, 60))],
)
async def get_ticket(
    ticket_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get a support ticket with all messages (owner only)."""
    ticket = await _get_user_ticket(session, ticket_id, user.id)
    messages = await _build_messages(session, ticket.id)
    return TicketResponse(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        category=ticket.category,
        subject=ticket.subject,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        messages=messages,
    )


@router.post(
    "/v1/support/tickets/{ticket_id}/reply",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit(20, 3600))],
)
async def reply_to_ticket(
    ticket_id: UUID,
    body: ReplyBody,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Reply to a support ticket (owner only, open or in_progress)."""
    ticket = await _get_user_ticket(session, ticket_id, user.id)

    if ticket.status not in ("open", "in_progress"):
        raise AppError("TICKET_CLOSED", "Cannot reply to a closed or resolved ticket", 400)

    message = SupportMessage(
        ticket_id=ticket.id,
        user_id=user.id,
        is_admin=False,
        body=body.message,
    )
    session.add(message)
    ticket.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(message)

    return MessageResponse(
        id=message.id,
        is_admin=False,
        body=message.body,
        created_at=message.created_at,
        author_name=user.username,
    )


@router.post(
    "/v1/support/tickets/{ticket_id}/close",
    response_model=TicketResponse,
    dependencies=[Depends(rate_limit(10, 3600))],
)
async def close_ticket(
    ticket_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Close a support ticket (owner only, open or in_progress)."""
    ticket = await _get_user_ticket(session, ticket_id, user.id)

    if ticket.status not in ("open", "in_progress"):
        raise AppError("TICKET_CANNOT_CLOSE", "Only open or in-progress tickets can be closed", 400)

    ticket.status = "closed"
    ticket.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(ticket)

    messages = await _build_messages(session, ticket.id)
    return TicketResponse(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        category=ticket.category,
        subject=ticket.subject,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        messages=messages,
    )


# ---- Admin Endpoints ----


@router.get(
    "/v1/admin/support/tickets",
    response_model=list[TicketListItem],
    dependencies=[Depends(rate_limit(30, 60))],
)
async def admin_list_tickets(
    status: str | None = Query(None),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all support tickets with optional filters."""
    valid_statuses = {"open", "in_progress", "resolved", "closed"}
    valid_categories = {"account", "publishing", "reviews", "billing", "bug", "other"}

    if status and status not in valid_statuses:
        raise AppError("INVALID_FILTER", f"Invalid status filter: {status}", 400)
    if category and category not in valid_categories:
        raise AppError("INVALID_FILTER", f"Invalid category filter: {category}", 400)

    query = select(SupportTicket)
    if status:
        query = query.where(SupportTicket.status == status)
    if category:
        query = query.where(SupportTicket.category == category)

    offset = (page - 1) * per_page
    query = query.order_by(SupportTicket.updated_at.desc()).offset(offset).limit(per_page)

    result = await session.execute(query)
    tickets = result.scalars().all()
    if not tickets:
        return []

    ticket_ids = [t.id for t in tickets]

    count_result = await session.execute(
        select(SupportMessage.ticket_id, func.count(SupportMessage.id))
        .where(SupportMessage.ticket_id.in_(ticket_ids))
        .group_by(SupportMessage.ticket_id)
    )
    count_map = {row[0]: row[1] for row in count_result.all()}

    last_msg_result = await session.execute(
        text("""
            SELECT DISTINCT ON (ticket_id) ticket_id, is_admin
            FROM support_messages
            WHERE ticket_id = ANY(:ids)
            ORDER BY ticket_id, created_at DESC
        """),
        {"ids": ticket_ids},
    )
    last_admin_map = {row[0]: row[1] for row in last_msg_result.all()}

    # Batch-load usernames for admin view
    user_ids = list({t.user_id for t in tickets})
    user_result = await session.execute(
        select(User.id, User.username).where(User.id.in_(user_ids))
    )
    username_map = {row.id: row.username for row in user_result.all()}

    return [
        _ticket_list_item(
            t, count_map.get(t.id, 0), last_admin_map.get(t.id, False),
            username=username_map.get(t.user_id),
        )
        for t in tickets
    ]


@router.get(
    "/v1/admin/support/tickets/{ticket_id}",
    response_model=TicketResponse,
    dependencies=[Depends(rate_limit(30, 60))],
)
async def admin_get_ticket(
    ticket_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Get a support ticket with all messages (admin)."""
    ticket = await _get_any_ticket(session, ticket_id)
    messages = await _build_messages(session, ticket.id)
    return TicketResponse(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        category=ticket.category,
        subject=ticket.subject,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        messages=messages,
    )


@router.post(
    "/v1/admin/support/tickets/{ticket_id}/reply",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit(30, 60))],
)
async def admin_reply_to_ticket(
    ticket_id: UUID,
    body: ReplyBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Reply to a support ticket as admin (Support Team)."""
    ticket = await _get_any_ticket(session, ticket_id)

    if ticket.status == "closed":
        raise AppError("TICKET_CLOSED", "Cannot reply to a closed ticket", 400)

    message = SupportMessage(
        ticket_id=ticket.id,
        user_id=user.id,
        is_admin=True,
        body=body.message,
    )
    session.add(message)
    ticket.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(message)
    await session.refresh(ticket)

    # Notify ticket owner about admin reply
    background_tasks.add_task(
        _notify_user_admin_reply, ticket.user_id, ticket.ticket_number, str(ticket.id),
    )

    return MessageResponse(
        id=message.id,
        is_admin=True,
        body=message.body,
        created_at=message.created_at,
        author_name="Support Team",
    )


@router.post(
    "/v1/admin/support/tickets/{ticket_id}/status",
    dependencies=[Depends(rate_limit(30, 60))],
)
async def admin_set_status(
    ticket_id: UUID,
    body: AdminSetStatusBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Change the status of a support ticket (admin)."""
    ticket = await _get_any_ticket(session, ticket_id)

    old_status = ticket.status
    ticket.status = body.status
    ticket.updated_at = datetime.now(timezone.utc)
    if body.status == "resolved":
        ticket.resolved_at = datetime.now(timezone.utc)
    await session.commit()

    # Notify user of status change
    if old_status != body.status:
        background_tasks.add_task(
            _notify_user_status_change, ticket.user_id, ticket.ticket_number,
            body.status, str(ticket.id),
        )

    return {"status": ticket.status, "ticket_id": str(ticket.id)}


# ---- Background email tasks ----


async def _notify_admins_new_ticket(
    ticket_number: int, subject: str,
    ticket_id: str | None = None, category: str | None = None, username: str | None = None,
) -> None:
    try:
        from app.shared.email import get_admin_emails, send_new_support_ticket_admin_email
        admin_emails = await get_admin_emails()
        for email in admin_emails:
            await send_new_support_ticket_admin_email(
                email, ticket_number, subject,
                ticket_id=ticket_id, category=category, username=username,
            )
    except Exception:
        logger.warning("Failed to send new ticket admin notification", exc_info=True)


async def _notify_user_admin_reply(user_id: UUID, ticket_number: int, ticket_id: str | None = None) -> None:
    try:
        from app.database import async_session_factory
        from app.auth.models import User as UserModel
        from app.shared.email import send_support_reply_email

        async with async_session_factory() as session:
            result = await session.execute(
                select(UserModel.email).where(UserModel.id == user_id)
            )
            row = result.scalar_one_or_none()
            if row:
                await send_support_reply_email(row, ticket_number, ticket_id=ticket_id)
    except Exception:
        logger.warning("Failed to send admin reply notification", exc_info=True)


async def _notify_user_status_change(
    user_id: UUID, ticket_number: int, new_status: str, ticket_id: str | None = None,
) -> None:
    try:
        from app.database import async_session_factory
        from app.auth.models import User as UserModel
        from app.shared.email import send_support_status_change_email

        async with async_session_factory() as session:
            result = await session.execute(
                select(UserModel.email).where(UserModel.id == user_id)
            )
            row = result.scalar_one_or_none()
            if row:
                await send_support_status_change_email(row, ticket_number, new_status, ticket_id=ticket_id)
    except Exception:
        logger.warning("Failed to send status change notification", exc_info=True)
