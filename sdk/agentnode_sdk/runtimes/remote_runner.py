"""Remote HTTP-based tool execution (stub for Phase 5)."""
from __future__ import annotations

from typing import Any

from agentnode_sdk.models import RunToolResult


def run_remote(
    slug: str,
    tool_name: str | None,
    *,
    timeout: float = 30.0,
    entry: dict,
    **kwargs: Any,
) -> RunToolResult:
    """Execute a tool via HTTP call to a remote endpoint.

    Not yet implemented -- will use httpx in Phase 5.
    """
    return RunToolResult(
        success=False,
        error="Remote execution is not yet implemented. Coming in a future release.",
        mode_used="remote",
    )
