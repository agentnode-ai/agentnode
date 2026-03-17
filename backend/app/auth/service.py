from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import ApiKey, User
from app.auth.security import (
    check_login_lockout,
    clear_failed_logins,
    create_access_token,
    create_purpose_token,
    create_refresh_token,
    decode_purpose_token,
    generate_api_key,
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    record_failed_login,
    revoke_refresh_jti,
    store_refresh_token,
    validate_refresh_jti,
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

    # Send welcome email with embedded verification link
    from app.shared.email import send_welcome_email
    verify_token = create_purpose_token(str(user.id), "email_verify", expire_hours=24)
    await send_welcome_email(email, username, verify_token)

    return user


async def login_user(session: AsyncSession, email: str, password: str, totp_code: str | None = None, redis=None) -> dict:
    # Check lockout before attempting authentication
    if redis:
        await check_login_lockout(redis, email)

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        if redis:
            await record_failed_login(redis, email)
        raise AppError("AUTH_INVALID_CREDENTIALS", "Invalid email or password", 401)

    # 2FA check
    if user.two_factor_enabled:
        if not totp_code:
            raise AppError("AUTH_2FA_REQUIRED", "2FA code required", 403)
        if not verify_totp(user.two_factor_secret, totp_code):
            if redis:
                await record_failed_login(redis, email)
            raise AppError("AUTH_2FA_INVALID", "Invalid 2FA code", 403)

    # Clear failed login counter on success
    if redis:
        await clear_failed_logins(redis, email)

    access_token = create_access_token(str(user.id))
    refresh_token, jti = create_refresh_token(str(user.id))

    # Store refresh token JTI in Redis for rotation
    if redis:
        await store_refresh_token(redis, str(user.id), jti)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "is_admin": user.is_admin,
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

    # Notify user about new API key
    user = await get_user_with_publisher(session, user_id)
    from app.shared.email import send_api_key_created_email
    await send_api_key_created_email(user.email, label, prefix)

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
        "provisioning_uri": get_totp_uri(secret, user.email),
    }


async def list_api_keys_for_user(session: AsyncSession, user_id: UUID) -> list[dict]:
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [
        {
            "id": k.id,
            "key_prefix": k.key_prefix,
            "label": k.label,
            "created_at": k.created_at.isoformat() if k.created_at else "",
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


async def revoke_api_key_for_user(session: AsyncSession, user_id: UUID, key_id: UUID) -> None:
    from datetime import datetime, timezone

    result = await session.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise AppError("API_KEY_NOT_FOUND", "API key not found", 404)
    if api_key.revoked_at is not None:
        raise AppError("API_KEY_ALREADY_REVOKED", "API key is already revoked", 400)

    api_key.revoked_at = datetime.now(timezone.utc)
    await session.commit()


async def verify_2fa(session: AsyncSession, user_id: UUID, totp_code: str) -> dict:
    user = await get_user_with_publisher(session, user_id)

    if not user.two_factor_secret:
        raise AppError("AUTH_2FA_NOT_SETUP", "Call /2fa/setup first", 400)

    if not verify_totp(user.two_factor_secret, totp_code):
        raise AppError("AUTH_2FA_INVALID", "Invalid 2FA code", 403)

    user.two_factor_enabled = True
    await session.commit()

    from app.shared.email import send_2fa_enabled_email
    await send_2fa_enabled_email(user.email)

    return {"two_factor_enabled": True}


# --- Sprint 2: Account management ---


async def change_password(
    session: AsyncSession, user_id: UUID, current_password: str, new_password: str
) -> dict:
    user = await get_user_with_publisher(session, user_id)

    if not verify_password(current_password, user.password_hash):
        raise AppError("AUTH_INVALID_CREDENTIALS", "Current password is incorrect", 401)

    user.password_hash = hash_password(new_password)
    await session.commit()

    from app.shared.email import send_password_changed_email
    await send_password_changed_email(user.email)

    return {"message": "Password changed successfully."}


async def request_password_reset(session: AsyncSession, email: str) -> dict:
    """Generate a password reset token and send email."""
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Always return success to prevent email enumeration
    if not user:
        return {"message": "If an account with that email exists, a reset link has been sent."}

    token = create_purpose_token(str(user.id), "password_reset", expire_hours=1)

    from app.shared.email import send_password_reset_email
    await send_password_reset_email(email, token)

    return {"message": "If an account with that email exists, a reset link has been sent."}


async def reset_password(session: AsyncSession, token: str, new_password: str) -> dict:
    try:
        user_id = decode_purpose_token(token, "password_reset")
    except Exception:
        raise AppError("AUTH_TOKEN_INVALID", "Reset token is invalid or expired", 400)

    result = await session.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", "User not found", 404)

    user.password_hash = hash_password(new_password)
    await session.commit()

    from app.shared.email import send_password_reset_confirmation_email
    await send_password_reset_confirmation_email(user.email)

    return {"message": "Password has been reset successfully."}


async def request_email_verification(session: AsyncSession, user_id: UUID) -> dict:
    user = await get_user_with_publisher(session, user_id)

    if user.is_email_verified:
        raise AppError("AUTH_EMAIL_ALREADY_VERIFIED", "Email is already verified", 400)

    token = create_purpose_token(str(user.id), "email_verify", expire_hours=24)

    from app.shared.email import send_verification_email
    await send_verification_email(user.email, token)

    return {"message": "Verification email sent."}


async def verify_email(session: AsyncSession, token: str) -> dict:
    try:
        user_id = decode_purpose_token(token, "email_verify")
    except Exception:
        raise AppError("AUTH_TOKEN_INVALID", "Verification token is invalid or expired", 400)

    result = await session.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", "User not found", 404)

    user.is_email_verified = True
    await session.commit()
    return {"email_verified": True}


async def update_profile(
    session: AsyncSession, user_id: UUID, username: str | None = None, email: str | None = None
) -> dict:
    user = await get_user_with_publisher(session, user_id)

    if username is not None and username != user.username:
        # Check uniqueness
        result = await session.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            raise AppError("AUTH_USERNAME_TAKEN", "Username already taken", 409)
        user.username = username

    old_email = user.email
    email_changed = False
    if email is not None and email != user.email:
        # Check uniqueness
        result = await session.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none():
            raise AppError("AUTH_EMAIL_TAKEN", "Email already registered", 409)
        user.email = email
        user.is_email_verified = False  # Re-verify after email change
        email_changed = True

    await session.commit()
    await session.refresh(user)

    if email_changed:
        from app.shared.email import send_email_changed_verify, send_email_changed_alert
        verify_token = create_purpose_token(str(user.id), "email_verify", expire_hours=24)
        await send_email_changed_verify(user.email, verify_token)
        await send_email_changed_alert(old_email, user.email)

    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "email_verified": user.is_email_verified,
    }
