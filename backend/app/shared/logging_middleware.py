"""Request logging middleware with trace IDs."""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("agentnode.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())[:8]
        request.state.trace_id = trace_id

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)

        logger.info(
            "%s %s %s %sms trace=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            trace_id,
        )

        response.headers["X-Trace-ID"] = trace_id
        return response
