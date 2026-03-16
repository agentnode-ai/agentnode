"""Universal REST API connector tool with retry logic using httpx."""

from __future__ import annotations

import base64
import time

import httpx


def _build_auth_headers(auth_type: str, auth_token: str) -> dict:
    """Build authentication headers based on the auth type.

    Args:
        auth_type: One of 'bearer', 'api_key', 'basic', or '' (none).
        auth_token: The token/key/credentials value.
            - bearer: the bearer token
            - api_key: value for X-API-Key header
            - basic: 'username:password' string

    Returns:
        dict of headers to merge into the request.
    """
    if not auth_type or not auth_token:
        return {}

    auth_type = auth_type.lower().strip()

    if auth_type == "bearer":
        return {"Authorization": f"Bearer {auth_token}"}

    if auth_type == "api_key":
        return {"X-API-Key": auth_token}

    if auth_type == "basic":
        encoded = base64.b64encode(auth_token.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}

    return {}


def _parse_response_body(response: httpx.Response):
    """Attempt to parse the response body as JSON; fall back to text."""
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return response.json()
        except Exception:
            pass
    return response.text


def run(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body: dict | None = None,
    auth_type: str = "",
    auth_token: str = "",
    timeout: int = 30,
    retries: int = 3,
) -> dict:
    """Make an HTTP request to any REST API with authentication and retry logic.

    Args:
        url: The full URL to send the request to.
        method: HTTP method ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS').
        headers: Optional dict of custom HTTP headers.
        body: Optional dict to send as JSON body (for POST/PUT/PATCH).
        auth_type: Authentication type: 'bearer', 'api_key', 'basic', or '' (none).
        auth_token: The authentication credential (token, API key, or user:pass).
        timeout: Request timeout in seconds (default 30).
        retries: Number of retries on transient failures (default 3).

    Returns:
        dict with:
            - status_code (int): HTTP response status code
            - headers (dict): Response headers
            - body (any): Parsed JSON or text response body
            - elapsed_ms (float): Request duration in milliseconds
            - success (bool): True if status code is 2xx
            - retries_used (int): Number of retries that were attempted
    """
    if not url:
        return {"success": False, "error": "Missing required parameter: url"}

    method = method.upper().strip()
    valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    if method not in valid_methods:
        return {
            "success": False,
            "error": f"Invalid HTTP method: {method}. Valid: {', '.join(sorted(valid_methods))}",
        }

    # Build headers
    request_headers: dict = {}
    if headers:
        request_headers.update(headers)

    # Add auth headers
    auth_headers = _build_auth_headers(auth_type, auth_token)
    request_headers.update(auth_headers)

    # Set Content-Type for requests with a body
    if body is not None and "Content-Type" not in request_headers:
        request_headers["Content-Type"] = "application/json"

    # Transient status codes that warrant a retry
    retryable_statuses = {408, 429, 500, 502, 503, 504}

    last_error: str | None = None
    retries_used = 0
    max_attempts = max(1, retries + 1)

    for attempt in range(max_attempts):
        try:
            start_time = time.monotonic()

            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                request_kwargs: dict = {
                    "method": method,
                    "url": url,
                    "headers": request_headers,
                }
                if body is not None and method in {"POST", "PUT", "PATCH"}:
                    request_kwargs["json"] = body

                response = client.request(**request_kwargs)

            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

            # Determine if this is a retryable failure
            if response.status_code in retryable_statuses and attempt < max_attempts - 1:
                retries_used += 1
                # Exponential backoff: 0.5s, 1s, 2s, ...
                backoff = min(0.5 * (2 ** attempt), 10)

                # Respect Retry-After header if present
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        backoff = max(backoff, float(retry_after))
                    except ValueError:
                        pass

                time.sleep(backoff)
                continue

            # Build response headers dict (limit size)
            resp_headers = dict(response.headers)

            return {
                "success": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "headers": resp_headers,
                "body": _parse_response_body(response),
                "elapsed_ms": elapsed_ms,
                "retries_used": retries_used,
            }

        except httpx.TimeoutException:
            last_error = f"Request timed out after {timeout}s"
            retries_used += 1
            if attempt < max_attempts - 1:
                time.sleep(min(0.5 * (2 ** attempt), 10))
                continue

        except httpx.ConnectError as exc:
            last_error = f"Connection error: {exc}"
            retries_used += 1
            if attempt < max_attempts - 1:
                time.sleep(min(0.5 * (2 ** attempt), 10))
                continue

        except Exception as exc:
            # Non-retryable error
            return {
                "success": False,
                "error": str(exc),
                "retries_used": retries_used,
            }

    return {
        "success": False,
        "error": last_error or "Request failed after all retries",
        "retries_used": retries_used,
    }
