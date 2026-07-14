"""Sandbox-smoke result model + evaluator (2c-1 gate wiring, 2c-4a freshness).

Defines the ``SmokeResult`` contract the executor (2c-2 npm / 2c-3 PyPI, both
host-verified) produces, and derives the ``sandbox_smoke`` gate from it. This
module runs nothing itself — the executor lives in ``mcp.smoke_executor`` and is
INERT unless ``MCP_SMOKE_MODE=container``. With no result the gate stays a
``future`` blocker, so ``auto_publish_eligible`` stays False (in prod there is no
smoke result while the executor is disabled) and MCP stays review-gated. A fresh
passing smoke makes the gate pass; a stale one is downgraded (2c-4a).

No I/O here: a caller supplies the already-collected ``SmokeResult`` dict (or
None) plus, for the gate, the current binding keys + clock. Pure + table-testable.

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

from datetime import datetime, timezone

# Status values a SmokeResult can carry.
PASSED = "passed"
FAILED = "failed"
UNAVAILABLE = "unavailable"  # backend/image/runtime missing — infra gap, review
SKIPPED = "skipped"  # policy (credentialed/private/high-risk) — review-fallback
RUNNING = "running"  # a recheck/first run is in progress
NOT_RUN = "not_run"  # no executor / no result yet — today's default

# 2c-4a — derived freshness states (a stored passed smoke that is no longer valid).
# These are NOT stored on the SmokeResult; evaluate_gates derives them from a
# freshness check and downgrades a stale `passed` to one of these for the gate.
EXPIRED = "expired"  # TTL elapsed — recheck needed, not a package fault
KEY_MISMATCH = "key_mismatch"  # version/command/image/schema changed — recheck

# A genuine failure is an OBJECTIVE fault of the submission (the server is broken)
# UNLESS it is a known-transient/ambiguous reason, which is retryable /
# review-fallback. Only UNAMBIGUOUS brokenness is a hard, objective failure:
# startup_crash, protocol_error, and tools_list_failed AFTER a successful
# initialize. initialize_failed is ambiguous under runtime network=none / no
# credentials (a legit server may fail init benignly), and timeout /
# excessive_output / internal_error are transient — all route to review, never a
# false hard-reject (they still block auto-publish). (Founder decision D6, 2c-2.)
TRANSIENT_FAILURES: frozenset[str] = frozenset(
    {
        "install_failed",
        "registry_unavailable",
        "timeout",
        "backend_unavailable",
        "initialize_failed",
        "excessive_output",
        "internal_error",
    }
)

_FAILURE_TEXT = {
    "install_failed": "sandbox install failed",
    "registry_unavailable": "registry unavailable during smoke — retry",
    "timeout": "sandbox smoke timed out",
    "backend_unavailable": "sandbox backend unavailable",
    "excessive_output": "sandbox smoke produced too much output",
    "internal_error": "internal smoke runner error",
    "initialize_failed": "MCP initialize failed in the sandbox",
    "tools_list_failed": "MCP tools/list failed in the sandbox",
    "startup_crash": "MCP server crashed on startup in the sandbox",
    "protocol_error": "MCP protocol error in the sandbox",
    # G3 — host RAM/disk was below the safe threshold; the smoke was NOT run (no
    # container). Transient/review, never a hard package fault.
    "resource_unavailable": "host resources low (RAM/disk) — smoke deferred; review/recheck",
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


# 2c-4a — the keys a passed smoke is bound to. If any differs from the current
# submission, the stored smoke is for a different artifact/runtime and must be
# rechecked. (registry is stored on the SmokeResult as ``runtime``.)
BINDING_KEYS: tuple[str, ...] = (
    "runtime",
    "package",
    "version",
    "command_hash",
    "image_digest",
    "run_model",
    "schema_version",
)


def evaluate_smoke_freshness(
    smoke: dict | None, current_keys: dict | None, now: datetime
) -> str:
    """Pure freshness verdict for a stored SmokeResult against the current
    submission's binding keys + clock. Returns one of:
    fresh | expired | key_mismatch | running | unavailable | not_passed | none.

    Only a ``passed`` smoke is freshness-checked; other statuses are handed back
    for normal handling. No I/O — ``current_keys`` and ``now`` are supplied.
    """
    smoke = smoke or {}
    status = smoke.get("status") or NOT_RUN
    if status == RUNNING:
        return RUNNING
    if status == UNAVAILABLE:
        return UNAVAILABLE
    if status != PASSED:
        return "not_passed"
    keys = current_keys or {}
    for k in BINDING_KEYS:
        if smoke.get(k) != keys.get(k):
            return KEY_MISMATCH
    exp = smoke.get("expires_at")
    if exp:
        try:
            e = datetime.fromisoformat(exp)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            if e <= now:
                return EXPIRED
        except Exception:
            pass
    return "fresh"


def derive_smoke_evidence(smoke: dict | None, *, freshness: str | None = None) -> dict:
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

    # 2c-4a: a passed smoke only counts while fresh. A caller-supplied freshness
    # verdict downgrades a stale passed smoke to expired / key_mismatch so the
    # gate no longer passes (review / recheck-needed, NOT a hard package fault).
    if status == PASSED and freshness in (EXPIRED, KEY_MISMATCH):
        status = freshness

    if status == PASSED:
        passed, ran, future, review_fallback = True, True, False, False
        reason = ""
    elif status in (EXPIRED, KEY_MISMATCH):
        passed, ran, future, review_fallback = False, True, True, True
        reason = (
            "sandbox smoke expired (TTL) — recheck needed"
            if status == EXPIRED
            else "sandbox smoke is stale (package/command/image/schema changed) "
            "— recheck needed"
        )
    elif status == RUNNING:
        passed, ran, future, review_fallback = False, False, True, False
        reason = "sandbox smoke in progress"
    elif status == FAILED:
        passed, ran = False, True
        transient = failure_reason in TRANSIENT_FAILURES
        # transient -> retry/review (future); hard -> objective blocker (not future)
        future = transient
        review_fallback = transient
        reason = _failure_text(failure_reason)
    elif status == UNAVAILABLE:
        passed, ran, future, review_fallback = False, False, True, True
        # G3: a host-resource shortfall reuses the UNAVAILABLE status (future/review)
        # — same objective-block-free semantics, just a clearer reason string.
        reason = (
            _failure_text("resource_unavailable")
            if failure_reason == "resource_unavailable"
            else "sandbox smoke unavailable (runtime/image/backend missing) — review"
        )
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
        "run_model": smoke.get("run_model"),
        "schema_version": smoke.get("schema_version"),
        "failure_reason": failure_reason,
        "protocol_stage": smoke.get("protocol_stage"),
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
