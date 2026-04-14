"""Tool execution dispatcher -- routes to the appropriate runtime.

Provides ``run_tool()`` -- the main entry point for running installed
AgentNode tools. Routes based on the ``runtime`` field in the lockfile.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import resolve_runtime, check_run, audit_decision

logger = logging.getLogger(__name__)

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
    **kwargs: Any,
) -> RunToolResult:
    """Run an installed tool, dispatching to the appropriate runtime.

    Args:
        slug: Package slug (e.g. ``"csv-analyzer-pack"``).
        tool_name: Tool name for multi-tool v0.2 packs.
        mode: ``"direct"``, ``"subprocess"``, or ``"auto"`` (Python runtime only).
        timeout: Maximum wall-clock seconds for execution.
        lockfile_path: Override path to ``agentnode.lock``.
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

    # Pre-execution policy check
    decision = check_run(slug, tool_name, kwargs, entry, interactive=True)
    audit_decision(
        decision, "run_tool", slug,
        tool_name=tool_name,
        trust_level=entry.get("trust_level"),
    )
    if decision.action == "deny":
        return RunToolResult(
            success=False, error=decision.reason, mode_used="policy_denied",
        )
    if decision.action == "prompt":
        return RunToolResult(
            success=False,
            error=f"Policy requires approval: {decision.reason}",
            mode_used="policy_prompt",
        )

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
        return run_python(slug, tool_name, mode=mode, timeout=timeout,
                          entry=entry, lockfile_path=lockfile_path, **kwargs)
    elif runtime == "mcp":
        from agentnode_sdk.runtimes.mcp_runner import run_mcp
        return run_mcp(slug, tool_name, timeout=timeout, entry=entry, **kwargs)
    elif runtime == "remote":
        from agentnode_sdk.runtimes.remote_runner import run_remote
        return run_remote(slug, tool_name, timeout=timeout, entry=entry, **kwargs)
    else:
        return RunToolResult(
            success=False,
            error=f"Unsupported runtime: {runtime!r}",
            mode_used=runtime,
        )


def _get_lockfile_entry(slug: str, lockfile_path: Path | None) -> dict:
    """Read the lockfile entry for a package."""
    data = read_lockfile(lockfile_path)
    return data.get("packages", {}).get(slug, {})
