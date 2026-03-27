import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import jwt
import pyotp
from fastapi import Response

from app.config import settings


# --- Passwords (bcrypt) ---

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# --- API Keys (SHA-256) ---

def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, sha256_hash)."""
    random_part = secrets.token_urlsafe(32)
    full_key = f"{settings.API_KEY_PREFIX}{random_part}"
    prefix = full_key[:8]
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, prefix, key_hash


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# --- JWT ---

def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "token_type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> tuple[str, str]:
    """Create a refresh token with a unique JTI. Returns (token, jti)."""
    jti = uuid4().hex
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "token_type": "refresh",
        "jti": jti,
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, jti


def decode_token(token: str) -> dict:
    """Decode and verify a JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


# --- TOTP (2FA) ---

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name="AgentNode")


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code)


# --- Purpose tokens (email verification, password reset) ---

def create_purpose_token(user_id: str, purpose: str, expire_hours: int = 24) -> str:
    """Create a short-lived JWT for a specific purpose (email_verify, password_reset)."""
    expire = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    payload = {
        "sub": str(user_id),
        "token_type": purpose,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_purpose_token(token: str, expected_purpose: str) -> str:
    """Decode a purpose token. Returns user_id. Raises on invalid/expired/wrong purpose."""
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("token_type") != expected_purpose:
        raise ValueError(f"Expected {expected_purpose} token")
    user_id = payload.get("sub")
    if not user_id:
        raise ValueError("Invalid token payload")
    return user_id


# --- httpOnly Cookies ---

def set_auth_cookies(response: Response, access_token: str, refresh_token: str, *, is_admin: bool = False) -> None:
    """Set httpOnly cookies for web clients."""
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/v1/auth",  # Only sent to auth endpoints
    )
    # Non-httpOnly flag so frontend JS can detect login state
    response.set_cookie(
        key="logged_in",
        value="1",
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )
    # Non-httpOnly admin flag so frontend can show admin nav instantly
    if is_admin:
        response.set_cookie(
            key="is_admin",
            value="1",
            httponly=False,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            domain=settings.COOKIE_DOMAIN or None,
            max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            path="/",
        )
    else:
        response.delete_cookie(
            key="is_admin",
            domain=settings.COOKIE_DOMAIN or None,
            path="/",
        )


def clear_auth_cookies(response: Response) -> None:
    """Remove all auth cookies."""
    for name in ("access_token", "refresh_token", "logged_in", "is_admin"):
        response.delete_cookie(
            key=name,
            domain=settings.COOKIE_DOMAIN or None,
            path="/" if name != "refresh_token" else "/api/v1/auth",
        )


# --- Refresh Token Rotation (Redis) ---

async def store_refresh_token(redis, user_id: str, jti: str) -> None:
    """Store refresh token JTI in Redis with TTL."""
    key = f"refresh:{jti}"
    ttl = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
    await redis.set(key, user_id, ex=ttl)


async def validate_refresh_jti(redis, jti: str) -> str | None:
    """Check if a refresh token JTI is valid. Returns user_id or None."""
    key = f"refresh:{jti}"
    return await redis.get(key)


async def revoke_refresh_jti(redis, jti: str) -> None:
    """Revoke a single refresh token JTI."""
    await redis.delete(f"refresh:{jti}")


# --- Account Lockout (Redis) ---

async def check_login_lockout(redis, email: str) -> None:
    """Raise if account is locked out due to too many failed attempts."""
    key = f"lockout:{email}"
    locked = await redis.get(key)
    if locked:
        ttl = await redis.ttl(key)
        from app.shared.exceptions import AppError
        raise AppError(
            "AUTH_ACCOUNT_LOCKED",
            f"Account temporarily locked. Try again in {max(1, ttl // 60)} minutes.",
            429,
            details={"retry_after": ttl},
        )


async def record_failed_login(redis, email: str) -> None:
    """Increment failed login counter. Lock account after threshold."""
    counter_key = f"login_fails:{email}"
    count = await redis.incr(counter_key)
    if count == 1:
        await redis.expire(counter_key, settings.LOGIN_LOCKOUT_SECONDS)

    if count >= settings.LOGIN_MAX_ATTEMPTS:
        lockout_key = f"lockout:{email}"
        await redis.set(lockout_key, "1", ex=settings.LOGIN_LOCKOUT_SECONDS)
        await redis.delete(counter_key)


async def clear_failed_logins(redis, email: str) -> None:
    """Clear failed login counter on successful login."""
    await redis.delete(f"login_fails:{email}")
