"""Tool execution dispatcher -- routes to the appropriate runtime.

Provides ``run_tool()`` -- the main entry point for running installed
AgentNode tools. Routes based on the ``runtime`` field in the lockfile.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import resolve_runtime, check_run, check_risk_policies, audit_decision, _resolve_interactive, PolicyResult

logger = logging.getLogger(__name__)

TRUST_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _maybe_refresh_trust(slug: str, entry: dict, lockfile_path: Path | None) -> dict:
    """Re-fetch trust level from backend if cached value is older than TTL.

    On success: new trust level is applied and lockfile updated.
    On network failure: cached trust used with a warning.
    """
    import os
    from datetime import datetime, timezone

    last_check = entry.get("last_trust_check", entry.get("installed_at", ""))
    if not last_check:
        return entry
    try:
        checked_at = datetime.fromisoformat(last_check)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - checked_at).total_seconds()
        if age < TRUST_TTL_SECONDS:
            return entry
    except (ValueError, TypeError):
        return entry

    try:
        import httpx
        base = os.environ.get("AGENTNODE_API_URL", "https://api.agentnode.net")
        resp = httpx.get(f"{base}/packages/{slug}", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            new_trust = (
                data.get("publisher", {}).get("trust_level")
                or data.get("blocks", {}).get("trust", {}).get("publisher_trust_level")
                or entry.get("trust_level", "unverified")
            )
            old_trust = entry.get("trust_level", "unverified")
            entry["trust_level"] = new_trust
            entry["last_trust_check"] = datetime.now(timezone.utc).isoformat()
            if new_trust != old_trust:
                logger.info(
                    "Trust level for %s updated: %s -> %s",
                    slug, old_trust, new_trust,
                )
            try:
                from agentnode_sdk.installer import update_lockfile
                update_lockfile(slug, entry, path=lockfile_path)
            except Exception:
                pass
    except Exception:
        logger.debug("Trust refresh failed for %s, using cached value", slug, exc_info=True)
    return entry


# Reserved kwarg names that may reach ``**kwargs`` via runtime-internal
# forwarding paths (e.g. ``entry`` is set by the dispatcher). Most other
# reserved names (``mode``, ``timeout``, ``slug``, ``tool_name``,
# ``lockfile_path``) are captured by ``run_tool``'s own signature and can
# never reach the tool — that is the documented behaviour.
#
# P1-SDK5: reject the ones that CAN slip through so a caller who passes a
# tool argument that collides gets a loud error instead of a silent
# type mismatch deep in the runtime.
_RESERVED_RUN_TOOL_KWARGS = frozenset({"entry"})


def run_tool(
    slug: str,
    tool_name: str | None = None,
    *,
    mode: str = "auto",
    timeout: float = 30.0,
    lockfile_path: Path | None = None,
    confirmation_callback: Callable | None = None,
    **kwargs: Any,
) -> RunToolResult:
    """Run an installed tool, dispatching to the appropriate runtime.

    Args:
        slug: Package slug (e.g. ``"csv-analyzer-pack"``).
        tool_name: Tool name for multi-tool v0.2 packs.
        mode: ``"direct"``, ``"subprocess"``, or ``"auto"`` (Python runtime only).
        timeout: Maximum wall-clock seconds for execution.
        lockfile_path: Override path to ``agentnode.lock``.
        confirmation_callback: Called with ``(slug, tool_name, guard_decision, entry)``
            when guard returns ``"prompt"``. Return ``True`` to proceed,
            ``False`` to deny. If ``None``, prompt decisions are returned
            as ``policy_prompt`` without execution.
        **kwargs: Arguments forwarded to the tool function.

    Returns:
        :class:`RunToolResult` with execution details.
    """
    if mode not in ("direct", "subprocess", "auto"):
        raise ValueError(f"Unknown mode: {mode!r}. Use 'direct', 'subprocess', or 'auto'.")

    # P1-SDK5: reject reserved kwargs that would silently shadow run_tool
    # parameters if the caller tried to pass them to the tool function.
    collisions = _RESERVED_RUN_TOOL_KWARGS.intersection(kwargs)
    if collisions:
        raise TypeError(
            f"run_tool() received reserved kwarg name(s) {sorted(collisions)}; "
            "rename the tool argument(s) or use a wrapper function."
        )

    # Read lockfile entry
    entry = _get_lockfile_entry(slug, lockfile_path)

    # upgrade is a distribution/relationship type, NOT an execution model.
    # Runner and Policy ignore it. UI shows as "Add-on".
    if entry.get("package_type") == "upgrade":
        return RunToolResult(
            success=False,
            error=f"Package '{slug}' is an upgrade/add-on and cannot be executed directly.",
            mode_used="not_executable",
        )

    # Refresh trust level if TTL expired (best-effort, fail-open on network error)
    entry = _maybe_refresh_trust(slug, entry, lockfile_path)

    # Pre-execution policy check
    decision = check_run(slug, tool_name, kwargs, entry, interactive=_resolve_interactive())
    audit_decision(
        decision, "run_tool", slug,
        tool_name=tool_name,
        trust_level=entry.get("trust_level"),
    )
    if decision.action == "allow":
        risk_result = check_risk_policies(slug, entry, interactive=_resolve_interactive())
        if risk_result is not None:
            audit_decision(
                risk_result, "run_tool", slug,
                tool_name=tool_name,
                trust_level=entry.get("trust_level"),
            )
            if risk_result.action in ("prompt", "deny"):
                decision = risk_result

    # Guard Layer 4: action-type classification + policy (Phase 6.1)
    guard_decision = None
    if decision.action == "allow" and entry.get("package_type") != "skill":
        from agentnode_sdk.guard import check_action as guard_check_action
        guard_decision = guard_check_action(
            slug, tool_name, kwargs, entry,
            interactive=_resolve_interactive(),
        )
        _audit_guard_decision(guard_decision, slug, tool_name, entry)
        if guard_decision.action in ("prompt", "deny"):
            decision = PolicyResult(
                action=guard_decision.action,
                reason=guard_decision.reason,
                source=guard_decision.source,
            )

    # Guard Layer 5: rate limiting (Phase 6.1)
    if decision.action == "allow" and entry.get("package_type") != "skill":
        from agentnode_sdk.guard import check_rate_limit
        rl_decision = check_rate_limit(slug, tool_name, entry=entry)
        if rl_decision.action != "allow":
            _audit_guard_decision(rl_decision, slug, tool_name, entry, event_type="guard_rate_limit")
            decision = PolicyResult(
                action=rl_decision.action,
                reason=rl_decision.reason,
                source=rl_decision.source,
            )

    from agentnode_sdk.input_guard import validate_tool_input
    input_findings = validate_tool_input(slug, tool_name, kwargs, entry)
    if input_findings:
        for f in input_findings:
            logger.warning("input_guard: %s/%s: %s", slug, tool_name, f)

    # Input guard escalation: prompt-level findings block execution
    if input_findings and decision.action == "allow":
        prompt_findings = [f for f in input_findings if f.level == "prompt"]
        if prompt_findings:
            interactive = _resolve_interactive()
            ig_action = "prompt" if interactive else "deny"
            decision = PolicyResult(
                action=ig_action,
                reason=f"Input guard: {prompt_findings[0].message}",
                source="input_guard",
            )
            audit_decision(
                decision, "run_tool", slug,
                tool_name=tool_name,
                trust_level=entry.get("trust_level"),
            )

    policy_info = {
        "action": decision.action,
        "reason": decision.reason,
        "source": decision.source,
    }
    if input_findings:
        policy_info["input_warnings"] = [str(f) for f in input_findings]
    if guard_decision and guard_decision.action_types:
        policy_info["guard_action_types"] = guard_decision.action_types
        policy_info["guard_risk_level"] = guard_decision.risk_level
    if decision.action == "deny":
        if guard_decision and guard_decision.action == "deny":
            from agentnode_sdk.guard import format_guard_deny
            logger.info(
                "Guard denied: slug=%s tool=%s reason=%s",
                slug, tool_name, decision.reason,
            )
        return RunToolResult(
            success=False, error=decision.reason, mode_used="policy_denied",
            policy=policy_info,
        )
    if decision.action == "prompt":
        if confirmation_callback is not None and guard_decision is not None:
            approved = confirmation_callback(slug, tool_name, guard_decision, entry)
            audit_decision(
                PolicyResult(
                    action="allow" if approved else "deny",
                    reason="user_confirmed" if approved else "user_rejected",
                    source="guard.confirmation",
                ),
                "guard_confirmation", slug,
                tool_name=tool_name,
                trust_level=entry.get("trust_level"),
            )
            if approved:
                decision = PolicyResult(
                    action="allow",
                    reason="User confirmed execution",
                    source="guard.confirmation",
                )
                policy_info["action"] = "allow"
                policy_info["reason"] = "User confirmed execution"
                policy_info["source"] = "guard.confirmation"
            else:
                return RunToolResult(
                    success=False,
                    error="User rejected execution",
                    mode_used="policy_denied",
                    policy=policy_info,
                )
        else:
            return RunToolResult(
                success=False,
                error=f"Policy requires approval: {decision.reason}",
                mode_used="policy_prompt",
                policy=policy_info,
            )

    # Agent dispatch — package_type=agent gets its own runner.
    # Agent timeout is configured via agent.limits.max_runtime_seconds,
    # not run_tool()'s timeout parameter.
    if entry.get("package_type") == "agent":
        from agentnode_sdk.runtimes.agent_runner import run_agent
        res = run_agent(slug, entry=entry, **kwargs)
        res.policy = policy_info
        _audit_runtime_run(slug, tool_name, "agent", res, entry)
        return res

    # Resolve runtime (default: python for backward compat)
    runtime = resolve_runtime(entry) if entry else "python"

    # P1-SDK10: log which runtime/mode is actually used so callers can
    # confirm that mode='auto' resolved to subprocess without parsing the
    # RunToolResult after the fact.
    logger.info(
        "run_tool dispatch: slug=%s tool=%s runtime=%s mode=%s",
        slug, tool_name, runtime, mode,
    )

    if runtime == "python":
        from agentnode_sdk.runtimes.python_runner import run_python
        res = run_python(slug, tool_name, mode=mode, timeout=timeout,
                         entry=entry, lockfile_path=lockfile_path, **kwargs)
    elif runtime == "mcp":
        from agentnode_sdk.runtimes.mcp_runner import run_mcp
        res = run_mcp(slug, tool_name, timeout=timeout, entry=entry, **kwargs)
    elif runtime == "remote":
        from agentnode_sdk.runtimes.remote_runner import run_remote
        res = run_remote(slug, tool_name, timeout=timeout, entry=entry, **kwargs)
    else:
        return RunToolResult(
            success=False,
            error=f"Unsupported runtime: {runtime!r}",
            mode_used=runtime,
            policy=policy_info,
        )
    res.policy = policy_info
    _audit_runtime_run(slug, tool_name, runtime, res, entry)
    return res


def _audit_runtime_run(
    slug: str,
    tool_name: str | None,
    runtime: str,
    result: RunToolResult,
    entry: dict,
) -> None:
    """Audit the runtime dispatch result. Never crashes the caller."""
    try:
        reason = f"runtime={runtime} success={result.success}"
        if not result.success and result.error:
            error_summary = result.error.split(":")[0][:80]
            reason += f" error={error_summary}"
        audit_decision(
            PolicyResult(
                action="allow" if result.success else "deny",
                reason=reason,
                source="runner.dispatch",
            ),
            "runtime_run",
            slug,
            tool_name=tool_name,
            trust_level=entry.get("trust_level"),
        )
    except Exception:
        logger.debug("Failed to audit runtime dispatch", exc_info=True)


def _get_lockfile_entry(slug: str, lockfile_path: Path | None) -> dict:
    """Read the lockfile entry for a package."""
    data = read_lockfile(lockfile_path)
    return data.get("packages", {}).get(slug, {})


def _audit_guard_decision(
    guard_decision: Any,
    slug: str,
    tool_name: str | None,
    entry: dict,
    event_type: str = "guard_check",
) -> None:
    """Write a guard audit event."""
    try:
        from agentnode_sdk.guard import GuardDecision
        if not isinstance(guard_decision, GuardDecision):
            return
        audit_decision(
            PolicyResult(
                action=guard_decision.action,
                reason=guard_decision.reason,
                source=guard_decision.source,
            ),
            event_type,
            slug,
            tool_name=tool_name,
            trust_level=entry.get("trust_level"),
        )
    except Exception:
        logger.debug("Failed to audit guard decision", exc_info=True)
