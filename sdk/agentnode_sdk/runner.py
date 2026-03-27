"""Tool execution dispatcher -- routes to the appropriate runtime.

Provides ``run_tool()`` -- the main entry point for running installed
AgentNode tools. Routes based on the ``runtime`` field in the lockfile.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import resolve_runtime


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

    # Read lockfile entry
    entry = _get_lockfile_entry(slug, lockfile_path)

    # Resolve runtime (default: python for backward compat)
    runtime = resolve_runtime(entry) if entry else "python"

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
