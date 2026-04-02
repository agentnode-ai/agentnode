"""Redis-based sliding window rate limiter with FastAPI dependency factory.
Spec section 19."""

import time

from fastapi import Depends, Request, Response

from app.shared.exceptions import AppError


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _check_rate_limit(
    request: Request,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[int, int, int]:
    """Sliding window rate limiter. Returns (limit, remaining, reset_at)."""
    redis = request.app.state.redis
    redis_key = f"rate_limit:{key}"
    now = time.time()

    pipe = redis.pipeline()
    pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
    pipe.zadd(redis_key, {str(now): now})
    pipe.zcard(redis_key)
    pipe.expire(redis_key, window_seconds)
    results = await pipe.execute()

    current_count = results[2]
    remaining = max(0, max_requests - current_count)
    reset_at = int(now + window_seconds)

    if current_count > max_requests:
        raise AppError(
            "RATE_LIMITED",
            f"Rate limit exceeded. Max {max_requests} requests per {window_seconds}s.",
            429,
            details={"retry_after": window_seconds, "limit": max_requests, "window": window_seconds},
        )

    return max_requests, remaining, reset_at


def _set_headers(response: Response, limit: int, remaining: int, reset_at: int) -> None:
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_at)


def rate_limit(max_requests: int = 60, window_seconds: int = 60):
    """Rate limit by client IP + path. For unauthenticated endpoints."""
    async def _check(request: Request, response: Response):
        client_ip = _get_client_ip(request)
        key = f"{client_ip}:{request.url.path}"
        limit, remaining, reset_at = await _check_rate_limit(request, key, max_requests, window_seconds)
        _set_headers(response, limit, remaining, reset_at)

    return _check


def rate_limit_authenticated(max_requests: int = 120, window_seconds: int = 60):
    """Rate limit by user ID (falls back to IP if not authenticated)."""
    async def _check(request: Request, response: Response):
        user_id = getattr(request.state, "rate_limit_user_id", None)
        if user_id:
            key = f"user:{user_id}:{request.url.path}"
        else:
            key = f"{_get_client_ip(request)}:{request.url.path}"
        limit, remaining, reset_at = await _check_rate_limit(request, key, max_requests, window_seconds)
        _set_headers(response, limit, remaining, reset_at)

    return _check


async def check_login_rate_limits(request: Request, response: Response, email: str) -> None:
    """Dual rate limit for login: 10/min/IP AND 5/min/email."""
    client_ip = _get_client_ip(request)

    # Check IP limit (10/min)
    limit, remaining, reset_at = await _check_rate_limit(request, f"login:ip:{client_ip}", 10, 60)
    _set_headers(response, limit, remaining, reset_at)

    # Check email limit (5/min)
    await _check_rate_limit(request, f"login:email:{email}", 5, 60)
