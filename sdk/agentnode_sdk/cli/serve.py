"""CLI entry point for the AgentNode MCP server."""
from __future__ import annotations

import asyncio


def cmd_serve() -> int:
    from agentnode_sdk.mcp_server import run_stdio
    asyncio.run(run_stdio())
    return 0
