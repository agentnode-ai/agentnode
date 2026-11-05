"""CLI entry point for the AgentNode MCP server."""
from __future__ import annotations

import asyncio


def cmd_serve(packs: str = "") -> int:
    from agentnode_sdk import mcp_server

    chosen = {s.strip() for s in (packs or "").split(",") if s.strip()}
    mcp_server._PACKS = chosen or None
    asyncio.run(mcp_server.run_stdio())
    return 0
