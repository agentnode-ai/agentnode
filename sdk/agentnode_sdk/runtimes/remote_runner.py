"""Remote HTTP-based tool execution via CredentialHandle.

Executes tools by making authenticated HTTP calls to external APIs.
Credentials are resolved from environment variables and injected
via CredentialHandle — the token never leaves the handle object.

Security invariants:
- Domain validation before every request (S3, S8)
- Audit entry per remote call
- Timeout and retry with backoff
- No secrets in logs, exceptions, or results
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urljoin

from agentnode_sdk.credential_handle import AuthorizedResponse, CredentialHandle
from agentnode_sdk.credential_resolver import resolve_handle
from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import audit_decision, PolicyResult

logger = logging.getLogger("agentnode.remote_runner")

# Retry config: one retry on 5xx, with short backoff
_MAX_RETRIES = 1
_RETRY_BACKOFF_SECONDS = 1.0
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}

# Size warning thresholds (advisory only — never block)
_MAX_REQUEST_BYTES = 10_485_760   # 10 MB
_MAX_RESPONSE_BYTES = 52_428_800  # 50 MB


def run_remote(
    slug: str,
    tool_name: str | None,
    *,
    timeout: float = 30.0,
    entry: dict,
    **kwargs: Any,
) -> RunToolResult:
    """Execute a tool via HTTP call to a remote endpoint.

    Flow:
    1. Read remote_endpoint and connector config from lockfile entry
    2. Resolve credentials via env vars
    3. Map tool_name to HTTP path
    4. Make authenticated HTTP request via CredentialHandle
    5. Handle response, timeouts, retries
    6. Audit the call
    7. Return RunToolResult
    """
    t0 = time.monotonic()

    # --- 1. Read lockfile entry ---
    remote_endpoint = entry.get("remote_endpoint")
    if not remote_endpoint:
        return RunToolResult(
            success=False,
            error=f"No remote_endpoint in lockfile for '{slug}'",
            mode_used="remote",
        )

    connector = entry.get("connector") or {}
    provider = connector.get("provider", slug)
    auth_type = connector.get("auth_type", "api_key")
    scopes = connector.get("scopes", [])

    # --- 2. Resolve credentials ---
    allowed_domains = _extract_allowed_domains(remote_endpoint, connector)
    handle = resolve_handle(
        provider,
        auth_type,
        scopes=scopes,
        allowed_domains=allowed_domains,
    )

    if handle is None:
        env_key = f"AGENTNODE_CRED_{provider.upper().replace('-', '_')}"
        return RunToolResult(
            success=False,
            error=(
                f"No credential found for provider '{provider}'. "
                f"Set {env_key} environment variable."
            ),
            mode_used="remote",
        )

    # --- 3. Map tool to HTTP endpoint ---
    url, method = _resolve_tool_endpoint(remote_endpoint, tool_name, entry)

    # Extract domain once for audit (hostname only — no path/query/secrets)
    try:
        from urllib.parse import urlparse as _urlparse
        _audit_domain = (_urlparse(url).hostname or "unknown").lower()
    except Exception:
        _audit_domain = "unknown"

    # --- 3b. Method/action-type consistency check (advisory) ---
    action_type = _extract_tool_action_type(tool_name, entry)
    method_warnings = _check_method_action_consistency(method, action_type)
    if method_warnings:
        for w in method_warnings:
            logger.warning(
                "Method/action-type mismatch: %s (slug=%s, tool=%s)",
                w, slug, tool_name,
            )

    # --- 3c. Measure request size (advisory) ---
    request_size, request_size_unknown = _measure_request_size(kwargs)
    if request_size_unknown:
        logger.warning(
            "Cannot measure request size: slug=%s tool=%s",
            slug, tool_name,
        )
    elif request_size is not None and request_size > _MAX_REQUEST_BYTES:
        logger.warning(
            "Request size %d bytes exceeds limit %d: slug=%s tool=%s",
            request_size, _MAX_REQUEST_BYTES, slug, tool_name,
        )

    # --- 4-5. Make authenticated request with retries ---
    last_error: str | None = None
    last_status: int | None = None
    response_size: int | None = None

    for attempt in range(_MAX_RETRIES + 1):
        if attempt > 0:
            time.sleep(_RETRY_BACKOFF_SECONDS)
            logger.info(
                "Retrying remote call: slug=%s tool=%s attempt=%d",
                slug, tool_name, attempt + 1,
            )

        try:
            resp = handle.authorized_request(
                method,
                url,
                json=kwargs if kwargs else None,
                timeout=timeout,
            )
            last_status = resp.status_code
            response_size = _measure_response_size(resp)
            if response_size > _MAX_RESPONSE_BYTES:
                logger.warning(
                    "Response size %d bytes exceeds limit %d: slug=%s tool=%s",
                    response_size, _MAX_RESPONSE_BYTES, slug, tool_name,
                )

            if resp.status_code < 400:
                # Success
                elapsed = (time.monotonic() - t0) * 1000
                result = _parse_response(resp)

                _audit_remote_call(
                    slug, tool_name, provider,
                    method=method,
                    domain=_audit_domain,
                    status_code=resp.status_code,
                    duration_ms=elapsed,
                    success=True,
                    method_warnings=method_warnings,
                    request_size_bytes=request_size,
                    request_size_unknown=request_size_unknown,
                    response_size_bytes=response_size,
                )

                return RunToolResult(
                    success=True,
                    result=result,
                    mode_used="remote",
                    duration_ms=round(elapsed, 1),
                )

            # Retryable error
            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                last_error = f"HTTP {resp.status_code}"
                continue

            # Non-retryable error
            elapsed = (time.monotonic() - t0) * 1000
            error_body = _safe_error_body(resp)

            _audit_remote_call(
                slug, tool_name, provider,
                method=method,
                domain=_audit_domain,
                status_code=resp.status_code,
                duration_ms=elapsed,
                success=False,
                method_warnings=method_warnings,
                request_size_bytes=request_size,
                request_size_unknown=request_size_unknown,
                response_size_bytes=response_size,
            )

            return RunToolResult(
                success=False,
                error=f"Remote API returned HTTP {resp.status_code}: {error_body}",
                mode_used="remote",
                duration_ms=round(elapsed, 1),
            )

        except PermissionError as exc:
            # Domain validation failure
            elapsed = (time.monotonic() - t0) * 1000
            _audit_remote_call(
                slug, tool_name, provider,
                method=method,
                domain=_audit_domain,
                status_code=None,
                duration_ms=elapsed,
                success=False,
                method_warnings=method_warnings,
                request_size_bytes=request_size,
                request_size_unknown=request_size_unknown,
            )
            return RunToolResult(
                success=False,
                error=str(exc),
                mode_used="remote",
                duration_ms=round(elapsed, 1),
            )

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            is_timeout = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()

            _audit_remote_call(
                slug, tool_name, provider,
                method=method,
                domain=_audit_domain,
                status_code=None,
                duration_ms=elapsed,
                success=False,
                method_warnings=method_warnings,
                request_size_bytes=request_size,
                request_size_unknown=request_size_unknown,
            )

            return RunToolResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                mode_used="remote",
                duration_ms=round(elapsed, 1),
                timed_out=is_timeout,
            )

    # All retries exhausted
    elapsed = (time.monotonic() - t0) * 1000
    _audit_remote_call(
        slug, tool_name, provider,
        method=method,
        domain=_audit_domain,
        status_code=last_status,
        duration_ms=elapsed,
        success=False,
        method_warnings=method_warnings,
        request_size_bytes=request_size,
        request_size_unknown=request_size_unknown,
        response_size_bytes=response_size,
    )
    return RunToolResult(
        success=False,
        error=f"Remote call failed after {_MAX_RETRIES + 1} attempts: {last_error}",
        mode_used="remote",
        duration_ms=round(elapsed, 1),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_allowed_domains(remote_endpoint: str, connector: dict) -> list[str]:
    """Build allowed_domains from the remote endpoint and connector config."""
    from urllib.parse import urlparse

    domains = []

    # Primary: the remote endpoint's host
    try:
        parsed = urlparse(remote_endpoint)
        if parsed.hostname:
            domains.append(parsed.hostname)
    except Exception:
        pass

    # Additional: health_check endpoint domain
    health = connector.get("health_check", {})
    if isinstance(health, dict):
        endpoint = health.get("endpoint", "")
        if endpoint:
            try:
                parsed = urlparse(endpoint)
                if parsed.hostname and parsed.hostname not in domains:
                    domains.append(parsed.hostname)
            except Exception:
                pass

    return domains


def _resolve_tool_endpoint(
    remote_endpoint: str,
    tool_name: str | None,
    entry: dict,
) -> tuple[str, str]:
    """Map a tool name to (url, http_method).

    Resolution order:
    1. If tools[] has a matching entry with 'endpoint' and 'method' → use those
    2. Default: POST {remote_endpoint}/{tool_name}
    """
    tools = entry.get("tools", [])

    if tool_name:
        for t in tools:
            if t.get("name") == tool_name:
                ep = t.get("endpoint", f"/{tool_name}")
                method = t.get("method", "POST").upper()
                url = urljoin(remote_endpoint.rstrip("/") + "/", ep.lstrip("/"))
                return url, method

    # Default: POST to base endpoint with tool_name as path
    if tool_name:
        url = urljoin(remote_endpoint.rstrip("/") + "/", tool_name)
    else:
        url = remote_endpoint

    return url, "POST"


def _parse_response(resp: AuthorizedResponse) -> Any:
    """Parse response body — JSON if possible, raw text otherwise."""
    try:
        return json.loads(resp.body)
    except (json.JSONDecodeError, ValueError):
        return resp.body


def _safe_error_body(resp: AuthorizedResponse) -> str:
    """Extract a safe error summary from a failed response (no secrets)."""
    body = resp.body[:500] if resp.body else "(empty)"
    return body


def _measure_request_size(kwargs: dict[str, Any]) -> tuple[int | None, bool]:
    """Measure JSON-serialized request payload size in bytes.

    Returns (size_bytes, size_unknown). size_unknown=True if serialization fails.
    """
    if not kwargs:
        return 0, False
    try:
        payload = json.dumps(kwargs, separators=(",", ":"), default=str)
        return len(payload.encode("utf-8")), False
    except Exception:
        return None, True


def _measure_response_size(resp: AuthorizedResponse) -> int:
    """Measure response body size in bytes."""
    if not resp.body:
        return 0
    return len(resp.body.encode("utf-8"))


def _extract_tool_action_type(tool_name: str | None, entry: dict) -> str | None:
    """Extract action_type from the tool definition, if declared."""
    if not tool_name:
        return None
    for t in entry.get("tools", []):
        if t.get("name") == tool_name:
            return t.get("action_type")
    return None


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _check_method_action_consistency(method: str, action_type: str | None) -> list[str]:
    """Detect suspicious mismatches between HTTP method and declared action_type.

    Advisory only — never blocks execution, never overrides Guard.
    """
    warnings: list[str] = []
    m = method.upper()

    if action_type == "read" and m in _MUTATING_METHODS:
        warnings.append(f"action_type='read' with HTTP {m}")
    elif action_type == "delete" and m == "GET":
        warnings.append(f"action_type='delete' with HTTP GET")
    elif not action_type and m in ("DELETE", "PUT", "PATCH"):
        warnings.append(f"no action_type declared with HTTP {m}")

    return warnings


def _audit_remote_call(
    slug: str,
    tool_name: str | None,
    provider: str,
    *,
    method: str | None = None,
    domain: str | None = None,
    status_code: int | None,
    duration_ms: float | None = None,
    success: bool,
    method_warnings: list[str] | None = None,
    request_size_bytes: int | None = None,
    request_size_unknown: bool = False,
    response_size_bytes: int | None = None,
) -> None:
    """Audit a remote tool call. Never crashes the caller.

    Fields use ``remote_`` prefix to avoid collisions with core audit schema.
    Only safe metadata is logged — no URLs, paths, kwargs, bodies, or secrets.
    """
    try:
        if success:
            reason = f"Remote call completed with status {status_code}"
        elif status_code is not None:
            reason = f"remote_call status={status_code}"
        else:
            reason = "remote_call_failed"

        result = PolicyResult(
            action="allow" if success else "deny",
            reason=reason,
            source="remote_runner",
        )
        extra: dict[str, Any] = {
            "remote_method": method,
            "remote_domain": domain,
            "remote_status_code": status_code,
            "remote_duration_ms": round(duration_ms) if duration_ms is not None else None,
            "remote_provider": provider,
        }
        if method_warnings:
            extra["remote_method_mismatch"] = True
            extra["remote_method_warnings"] = method_warnings
        if request_size_unknown:
            extra["remote_request_size_unknown"] = True
        elif request_size_bytes is not None and request_size_bytes > _MAX_REQUEST_BYTES:
            extra["remote_request_size_bytes"] = request_size_bytes
            extra["remote_request_size_warning"] = True
            extra["remote_request_size_limit"] = _MAX_REQUEST_BYTES
        if response_size_bytes is not None and response_size_bytes > _MAX_RESPONSE_BYTES:
            extra["remote_response_size_bytes"] = response_size_bytes
            extra["remote_response_size_warning"] = True
            extra["remote_response_size_limit"] = _MAX_RESPONSE_BYTES
        audit_decision(
            result,
            "remote_run",
            slug,
            tool_name=tool_name,
            extra=extra,
        )
    except Exception:
        logger.debug("Failed to audit remote call for %s", slug, exc_info=True)
