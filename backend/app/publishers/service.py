from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.publishers.models import Publisher
from app.shared.exceptions import AppError


async def create_publisher(
    session: AsyncSession, user_id: UUID, display_name: str, slug: str,
    bio: str | None = None, website_url: str | None = None, github_url: str | None = None,
) -> Publisher:
    # Check if user already has a publisher
    result = await session.execute(select(Publisher).where(Publisher.user_id == user_id))
    if result.scalar_one_or_none():
        raise AppError("PUBLISHER_ALREADY_EXISTS", "User already has a publisher profile", 409)

    # Check slug uniqueness
    result = await session.execute(select(Publisher).where(Publisher.slug == slug))
    if result.scalar_one_or_none():
        raise AppError("PUBLISHER_SLUG_TAKEN", "Publisher slug already taken", 409)

    publisher = Publisher(
        user_id=user_id,
        display_name=display_name,
        slug=slug,
        bio=bio,
        website_url=website_url,
        github_url=github_url,
    )
    session.add(publisher)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError("PUBLISHER_SLUG_TAKEN", "Publisher slug already taken", 409)
    await session.refresh(publisher)

    # Send confirmation email
    from app.auth.models import User
    user_result = await session.execute(select(User).where(User.id == user_id))
    user_obj = user_result.scalar_one_or_none()
    if user_obj:
        from app.shared.email import send_publisher_created_email
        await send_publisher_created_email(user_obj.email, slug)

    return publisher


async def get_publisher_by_slug(session: AsyncSession, slug: str) -> Publisher:
    result = await session.execute(select(Publisher).where(Publisher.slug == slug))
    publisher = result.scalar_one_or_none()
    if not publisher:
        raise AppError("PUBLISHER_NOT_FOUND", f"Publisher '{slug}' not found", 404)
    return publisher
