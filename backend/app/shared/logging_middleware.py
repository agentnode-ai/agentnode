"""Request logging middleware with trace IDs (pure ASGI)."""
import logging
import time
import uuid

logger = logging.getLogger("agentnode.access")


class RequestLoggingMiddleware:
    """Pure ASGI middleware — no BaseHTTPMiddleware overhead.

    Generates a trace ID per request, logs method/path/status/duration,
    and injects an X-Trace-ID response header.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = str(uuid.uuid4())[:8]

        # Store trace_id on scope["state"] so downstream code
        # accessing request.state.trace_id still works (Starlette
        # reads request.state from scope["state"]).
        scope.setdefault("state", {})
        scope["state"]["trace_id"] = trace_id

        method = scope.get("method", "?")
        path = scope.get("path", "/")
        status_code = 500  # fallback if we never see a response start

        start = time.monotonic()

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                # Inject X-Trace-ID header
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", trace_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.info(
                "%s %s %s %sms trace=%s",
                method,
                path,
                status_code,
                duration_ms,
                trace_id,
            )
