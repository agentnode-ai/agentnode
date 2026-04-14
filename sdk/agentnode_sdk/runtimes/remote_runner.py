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

    # --- 4-5. Make authenticated request with retries ---
    last_error: str | None = None
    last_status: int | None = None

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

            if resp.status_code < 400:
                # Success
                elapsed = (time.monotonic() - t0) * 1000
                result = _parse_response(resp)

                _audit_remote_call(
                    slug, tool_name, provider,
                    status_code=resp.status_code,
                    success=True,
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
                status_code=resp.status_code,
                success=False,
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
                status_code=None,
                success=False,
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
                status_code=None,
                success=False,
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
        status_code=last_status,
        success=False,
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


def _audit_remote_call(
    slug: str,
    tool_name: str | None,
    provider: str,
    *,
    status_code: int | None,
    success: bool,
) -> None:
    """Audit a remote tool call. Never crashes the caller."""
    try:
        result = PolicyResult(
            action="allow" if success else "deny",
            reason=f"remote_call status={status_code}" if status_code else "remote_call_failed",
            source="remote_runner",
        )
        audit_decision(
            result,
            "run_tool",
            slug,
            tool_name=tool_name,
        )
    except Exception:
        logger.debug("Failed to audit remote call for %s", slug, exc_info=True)
