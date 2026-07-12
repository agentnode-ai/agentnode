"""Slice 2c-1 — sandbox-smoke result model + evaluator (advisory only, no exec).

Defines the ``SmokeResult`` contract a future executor (2c-2 npm / 2c-3 PyPI)
will produce, and derives the ``sandbox_smoke`` gate from it. NO mechanism runs
an MCP yet — no container, no install, no network, no MCP code execution. With no
result the gate stays a ``future`` blocker, identical to the hardcoded gate it
replaces, so ``auto_publish_eligible`` stays False for everything and MCP stays
review-gated.

No I/O here: a caller supplies the already-collected ``SmokeResult`` dict (or
None). This stays a pure, table-testable function — the 2a / 2b-1 pattern.

No migration: the result lives in the existing ``server_verification`` JSONB
(under the ``smoke`` key), and the derived gate lives in ``gate_result``.

SmokeResult (produced later by the executor):
    {
      "status": "passed" | "failed" | "unavailable" | "skipped" | "not_run",
      "runtime": "npm" | "pypi" | None,
      "package": str | None, "version": str | None, "command_hash": str | None,
      "initialized": bool, "tools_count": int | None, "duration_ms": int | None,
      "sandbox_backend": "docker" | "podman" | None, "image_digest": str | None,
      "failure_reason": str | None,   # set when status == "failed"
      "review_reason": str | None,    # set when status == "skipped"
      "checked_at": str | None, "expires_at": str | None, "recheck_at": str | None,
    }
"""

from __future__ import annotations

# Status values a SmokeResult can carry.
PASSED = "passed"
FAILED = "failed"
UNAVAILABLE = "unavailable"  # backend/image/runtime missing — infra gap, review
SKIPPED = "skipped"  # policy (credentialed/private/high-risk) — review-fallback
NOT_RUN = "not_run"  # no executor / no result yet — today's default

# A genuine failure is an OBJECTIVE fault of the submission (the server is broken)
# UNLESS it is a known-transient reason, which is retryable / review-fallback.
# Default: anything not explicitly transient counts as a hard, objective failure.
TRANSIENT_FAILURES: frozenset[str] = frozenset(
    {"install_failed", "registry_unavailable", "timeout", "backend_unavailable"}
)

_FAILURE_TEXT = {
    "install_failed": "sandbox install failed",
    "registry_unavailable": "registry unavailable during smoke — retry",
    "timeout": "sandbox smoke timed out",
    "backend_unavailable": "sandbox backend unavailable",
    "initialize_failed": "MCP initialize failed in the sandbox",
    "tools_list_failed": "MCP tools/list failed in the sandbox",
    "startup_crash": "MCP server crashed on startup in the sandbox",
    "protocol_error": "MCP protocol error in the sandbox",
}

_SKIP_TEXT = {
    "credentialed": "credentialed MCP — review-fallback (a smoke can't run without secrets)",
    "private": "private package — review-fallback",
    "high_risk": "high-risk permissions — review-fallback",
}


def _failure_text(reason: str | None) -> str:
    if reason in _FAILURE_TEXT:
        return _FAILURE_TEXT[reason]
    return f"sandbox smoke failed ({reason})" if reason else "sandbox smoke failed"


def _skip_text(reason: str | None) -> str:
    return _SKIP_TEXT.get(reason or "", "smoke skipped — review-fallback")


def derive_smoke_evidence(smoke: dict | None) -> dict:
    """Derive the ``sandbox_smoke`` gate inputs from a SmokeResult (or None).

    Returns {status, passed, ran, future, review_fallback, reason, evidence}.

    ``passed`` is True ONLY for a real ``passed`` smoke. ``future`` is False only
    when a real result exists that objectively decides the submission (a pass, or
    a hard failure); it stays True for not-run / unavailable / skipped / transient
    failures (so those read as review-fallback / pending-infra, not an objective
    fault). This keeps today's behavior exactly (None -> not_run -> future=True).
    """
    smoke = smoke or {}
    status = smoke.get("status") or NOT_RUN
    failure_reason = smoke.get("failure_reason")
    review_reason = smoke.get("review_reason")

    if status == PASSED:
        passed, ran, future, review_fallback = True, True, False, False
        reason = ""
    elif status == FAILED:
        passed, ran = False, True
        transient = failure_reason in TRANSIENT_FAILURES
        # transient -> retry/review (future); hard -> objective blocker (not future)
        future = transient
        review_fallback = transient
        reason = _failure_text(failure_reason)
    elif status == UNAVAILABLE:
        passed, ran, future, review_fallback = False, False, True, True
        reason = "sandbox smoke unavailable (runtime/image/backend missing) — review"
    elif status == SKIPPED:
        passed, ran, future, review_fallback = False, False, True, True
        reason = _skip_text(review_reason)
    else:  # NOT_RUN or any unknown status
        status = NOT_RUN
        passed, ran, future, review_fallback = False, False, True, False
        reason = "server-authoritative sandbox smoke not run yet"

    evidence: dict = {
        "status": status,
        "ran": ran,
        "runtime": smoke.get("runtime"),
        "package": smoke.get("package"),
        "version": smoke.get("version"),
        "command_hash": smoke.get("command_hash"),
        "initialized": bool(smoke.get("initialized")),
        "tools_count": smoke.get("tools_count"),
        "duration_ms": smoke.get("duration_ms"),
        "sandbox_backend": smoke.get("sandbox_backend"),
        "image_digest": smoke.get("image_digest"),
        "failure_reason": failure_reason,
    }
    for k in ("checked_at", "expires_at", "recheck_at"):
        if smoke.get(k):
            evidence[k] = smoke[k]

    return {
        "status": status,
        "passed": passed,
        "ran": ran,
        "future": future,
        "review_fallback": review_fallback,
        "reason": reason,
        "evidence": evidence,
    }
