from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import ApiKey, User
from app.auth.security import decode_token, hash_api_key
from app.database import get_session
from app.shared.exceptions import AppError


async def get_current_user(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Authenticate via X-API-Key header, Bearer JWT, or httpOnly cookie."""
    user = None

    if x_api_key:
        user = await _authenticate_api_key(session, x_api_key)
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        user = await _authenticate_jwt(session, token, expected_type="access")
    else:
        # Fallback: try httpOnly cookie
        cookie_token = request.cookies.get("access_token")
        if cookie_token:
            user = await _authenticate_jwt(session, cookie_token, expected_type="access")

    if not user:
        raise AppError("AUTH_INVALID_CREDENTIALS", "Missing or invalid authentication", 401)

    if user.is_banned:
        raise AppError("AUTH_ACCOUNT_BANNED", "Your account has been suspended", 403)

    return user


async def optional_current_user(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
) -> User | None:
    """Like get_current_user but returns None instead of 401 when unauthenticated."""
    try:
        if x_api_key:
            return await _authenticate_api_key(session, x_api_key)
        elif authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
            return await _authenticate_jwt(session, token, expected_type="access")
        else:
            cookie_token = request.cookies.get("access_token")
            if cookie_token:
                return await _authenticate_jwt(session, cookie_token, expected_type="access")
    except AppError:
        return None
    return None


async def require_publisher(user: User = Depends(get_current_user)) -> User:
    """Ensure the user has a publisher profile and is not suspended."""
    if not user.publisher:
        raise AppError("PUBLISHER_REQUIRED", "You must create a publisher profile first", 403)
    if user.publisher.is_suspended:
        raise AppError("PUBLISHER_SUSPENDED", "Your publisher account is suspended", 403)
    return user



async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Ensure the user is an admin."""
    if not user.is_admin:
        raise AppError("ADMIN_REQUIRED", "Admin privileges required", 403)
    return user


async def _authenticate_jwt(session: AsyncSession, token: str, expected_type: str = "access") -> User | None:
    try:
        payload = decode_token(token)
    except Exception:
        raise AppError("AUTH_TOKEN_EXPIRED", "Token is invalid or expired", 401)

    if payload.get("token_type") != expected_type:
        raise AppError("AUTH_INVALID_CREDENTIALS", f"Expected {expected_type} token", 401)

    user_id = payload.get("sub")
    if not user_id:
        return None

    result = await session.execute(
        select(User).options(selectinload(User.publisher)).where(User.id == UUID(user_id))
    )
    return result.scalar_one_or_none()


async def _authenticate_api_key(session: AsyncSession, key: str) -> User | None:
    prefix = key[:8]
    result = await session.execute(
        select(ApiKey).where(ApiKey.key_prefix == prefix)
    )
    candidates = result.scalars().all()

    key_hash = hash_api_key(key)
    for candidate in candidates:
        if candidate.key_hash_sha256 == key_hash:
            if candidate.revoked_at is not None:
                raise AppError("AUTH_API_KEY_REVOKED", "API key has been revoked", 401)
            # Update last_used_at
            candidate.last_used_at = datetime.now(timezone.utc)
            await session.commit()
            # Load user with publisher
            user_result = await session.execute(
                select(User).options(selectinload(User.publisher)).where(User.id == candidate.user_id)
            )
            return user_result.scalar_one_or_none()

    raise AppError("AUTH_API_KEY_INVALID", "Invalid API key", 401)
