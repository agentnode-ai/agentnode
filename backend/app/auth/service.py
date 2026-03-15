from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import ApiKey, User
from app.auth.security import (
    create_access_token,
    create_refresh_token,
    generate_api_key,
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    verify_password,
    verify_totp,
)
from app.shared.exceptions import AppError


async def register_user(session: AsyncSession, email: str, username: str, password: str) -> User:
    # Check email uniqueness
    result = await session.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise AppError("AUTH_EMAIL_TAKEN", "Email already registered", 409)

    # Check username uniqueness (case-insensitive)
    result = await session.execute(select(User).where(User.username == username.lower()))
    if result.scalar_one_or_none():
        raise AppError("AUTH_USERNAME_TAKEN", "Username already taken", 409)

    user = User(
        email=email,
        username=username.lower(),
        password_hash=hash_password(password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def login_user(session: AsyncSession, email: str, password: str, totp_code: str | None = None) -> dict:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        raise AppError("AUTH_INVALID_CREDENTIALS", "Invalid email or password", 401)

    # 2FA check
    if user.two_factor_enabled:
        if not totp_code:
            raise AppError("AUTH_2FA_REQUIRED", "2FA code required", 403)
        if not verify_totp(user.two_factor_secret, totp_code):
            raise AppError("AUTH_2FA_INVALID", "Invalid 2FA code", 403)

    return {
        "access_token": create_access_token(str(user.id)),
        "refresh_token": create_refresh_token(str(user.id)),
        "token_type": "bearer",
    }


async def create_api_key_for_user(session: AsyncSession, user_id: UUID, label: str | None = None) -> dict:
    full_key, prefix, key_hash = generate_api_key()

    api_key = ApiKey(
        user_id=user_id,
        key_prefix=prefix,
        key_hash_sha256=key_hash,
        label=label,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    return {
        "id": api_key.id,
        "key": full_key,
        "key_prefix": prefix,
        "label": label,
    }


async def get_user_with_publisher(session: AsyncSession, user_id: UUID) -> User:
    result = await session.execute(
        select(User).options(selectinload(User.publisher)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", "User not found", 404)
    return user


async def setup_2fa(session: AsyncSession, user_id: UUID) -> dict:
    user = await get_user_with_publisher(session, user_id)

    if user.two_factor_enabled:
        raise AppError("AUTH_2FA_ALREADY_ENABLED", "2FA is already enabled", 400)

    secret = generate_totp_secret()
    user.two_factor_secret = secret
    await session.commit()

    return {
        "secret": secret,
        "qr_uri": get_totp_uri(secret, user.email),
    }


async def verify_2fa(session: AsyncSession, user_id: UUID, totp_code: str) -> dict:
    user = await get_user_with_publisher(session, user_id)

    if not user.two_factor_secret:
        raise AppError("AUTH_2FA_NOT_SETUP", "Call /2fa/setup first", 400)

    if not verify_totp(user.two_factor_secret, totp_code):
        raise AppError("AUTH_2FA_INVALID", "Invalid 2FA code", 403)

    user.two_factor_enabled = True
    await session.commit()

    return {"two_factor_enabled": True}
