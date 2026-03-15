from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database import get_session
from app.publishers.schemas import CreatePublisherRequest, PublisherResponse
from app.publishers.service import create_publisher, get_publisher_by_slug

router = APIRouter(prefix="/v1/publishers", tags=["publishers"])


@router.post("", response_model=PublisherResponse, status_code=201)
async def create_publisher_route(
    body: CreatePublisherRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    publisher = await create_publisher(
        session, user.id, body.display_name, body.slug,
        bio=body.bio, website_url=body.website_url, github_url=body.github_url,
    )
    return PublisherResponse(
        id=publisher.id,
        display_name=publisher.display_name,
        slug=publisher.slug,
        bio=publisher.bio,
        trust_level=publisher.trust_level,
        website_url=publisher.website_url,
        github_url=publisher.github_url,
        packages_published_count=publisher.packages_published_count,
        created_at=publisher.created_at,
    )


@router.get("/{slug}", response_model=PublisherResponse)
async def get_publisher(slug: str, session: AsyncSession = Depends(get_session)):
    publisher = await get_publisher_by_slug(session, slug)
    return PublisherResponse(
        id=publisher.id,
        display_name=publisher.display_name,
        slug=publisher.slug,
        bio=publisher.bio,
        trust_level=publisher.trust_level,
        website_url=publisher.website_url,
        github_url=publisher.github_url,
        packages_published_count=publisher.packages_published_count,
        created_at=publisher.created_at,
    )
