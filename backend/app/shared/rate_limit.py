"""Redis-based sliding window rate limiter with FastAPI dependency factory."""
import time
from typing import Callable

from fastapi import Depends, Request

from app.shared.exceptions import AppError


async def check_rate_limit(
    request: Request,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    """Redis-based sliding window rate limiter."""
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
    if current_count > max_requests:
        raise AppError(
            "RATE_LIMITED",
            f"Rate limit exceeded. Max {max_requests} requests per {window_seconds}s.",
            429,
        )


def rate_limit(max_requests: int = 60, window_seconds: int = 60, key_func: Callable | None = None):
    """FastAPI dependency factory for rate limiting.

    Usage:
        @router.get("/endpoint", dependencies=[Depends(rate_limit(30, 60))])
    """
    async def _check(request: Request):
        if key_func:
            key = key_func(request)
        else:
            # Default: rate limit by client IP + path
            client_ip = request.client.host if request.client else "unknown"
            key = f"{client_ip}:{request.url.path}"
        await check_rate_limit(request, key, max_requests, window_seconds)

    return _check
