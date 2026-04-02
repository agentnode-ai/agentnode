from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database import get_session
from app.publishers.schemas import (
    CreatePublisherRequest,
    PublisherResponse,
    RegisterSigningKeyRequest,
    SigningKeyResponse,
    UpdatePublisherRequest,
)
from app.publishers.service import create_publisher, get_publisher_by_slug
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit

router = APIRouter(prefix="/v1/publishers", tags=["publishers"])


@router.post("", response_model=PublisherResponse, status_code=201, dependencies=[Depends(rate_limit(5, 60))])
async def create_publisher_route(
    body: CreatePublisherRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    publisher = await create_publisher(
        session, user.id, body.display_name, body.slug,
        bio=body.bio, website_url=body.website_url, github_url=body.github_url,
        background_tasks=background_tasks,
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


@router.get("/{slug}", response_model=PublisherResponse, dependencies=[Depends(rate_limit(60, 60))])
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


@router.get("/{slug}/signing-key", response_model=SigningKeyResponse, dependencies=[Depends(rate_limit(60, 60))])
async def get_signing_key(slug: str, session: AsyncSession = Depends(get_session)):
    """Get the publisher's signing public key. Public endpoint."""
    publisher = await get_publisher_by_slug(session, slug)
    if not publisher.signing_public_key or not publisher.signing_key_registered_at:
        raise AppError("SIGNING_KEY_NOT_FOUND", "Publisher has no registered signing key", 404)
    return SigningKeyResponse(
        public_key=publisher.signing_public_key,
        registered_at=publisher.signing_key_registered_at,
    )


@router.put("/{slug}/signing-key", response_model=SigningKeyResponse, dependencies=[Depends(rate_limit(5, 60))])
async def register_signing_key(
    slug: str,
    body: RegisterSigningKeyRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Register or replace an Ed25519 signing public key. Only the publisher owner can call this."""
    publisher = await get_publisher_by_slug(session, slug)
    if publisher.user_id != user.id:
        raise AppError("PUBLISHER_NOT_OWNED", "You do not own this publisher", 403)

    publisher.signing_public_key = body.public_key
    publisher.signing_key_registered_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(publisher)

    return SigningKeyResponse(
        public_key=publisher.signing_public_key,
        registered_at=publisher.signing_key_registered_at,
    )


@router.put("/{slug}", response_model=PublisherResponse, dependencies=[Depends(rate_limit(10, 60))])
async def update_publisher(
    slug: str,
    body: UpdatePublisherRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Update publisher profile. Only the owner can update."""
    publisher = await get_publisher_by_slug(session, slug)
    if publisher.user_id != user.id:
        raise AppError("PUBLISHER_NOT_OWNED", "You do not own this publisher", 403)

    if body.display_name is not None:
        publisher.display_name = body.display_name
    if body.bio is not None:
        publisher.bio = body.bio
    if body.website_url is not None:
        publisher.website_url = body.website_url
    if body.github_url is not None:
        publisher.github_url = body.github_url

    await session.commit()
    await session.refresh(publisher)

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
