"""CLI entry point for the AgentNode MCP server."""
from __future__ import annotations

import asyncio


def cmd_serve() -> int:
    from agentnode_sdk.mcp_server import mcp_app
    asyncio.run(mcp_app.run_stdio_async())
    return 0
