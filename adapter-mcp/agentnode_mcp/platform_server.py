"""MCP server that exposes AgentNode platform capabilities as MCP tools.

Unlike the pack server (server.py) which wraps installed packs,
this server exposes AgentNode's own features:
  - agentnode_search: Search the AgentNode registry
  - agentnode_resolve: Resolve capabilities to packages
  - agentnode_explain: Get detailed package information
  - agentnode_install_info: Get install metadata for a package

Usage:
    agentnode-mcp-platform --api-url https://api.agentnode.net

    # In claude_desktop_config.json:
    {
        "mcpServers": {
            "agentnode-platform": {
                "command": "agentnode-mcp-platform",
                "args": ["--api-url", "https://api.agentnode.net"]
            }
        }
    }
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import httpx

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.agentnode.net"


def create_platform_server(api_url: str, api_key: str | None = None) -> Server:
    """Create an MCP server that exposes AgentNode platform as tools."""
    app = Server("agentnode-platform")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    def _api_request(method: str, path: str, body: dict | None = None) -> dict:
        with httpx.Client(base_url=api_url, headers=headers, timeout=30) as client:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = client.post(path, json=body or {})
            resp.raise_for_status()
            return resp.json()

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="agentnode_search",
                description="Search the AgentNode package registry for AI agent capabilities. Returns packages matching the query with trust levels and compatibility info.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query (name, capability, or keyword)"},
                        "capability_id": {"type": "string", "description": "Filter by specific capability ID (e.g. pdf_extraction, web_search)"},
                        "framework": {"type": "string", "description": "Filter by framework (langchain, crewai, generic)"},
                        "trust_level": {"type": "string", "description": "Minimum trust level (unverified, verified, trusted, curated)"},
                    },
                    "required": [],
                },
            ),
            Tool(
                name="agentnode_resolve",
                description="Resolve capability gaps to ranked package recommendations. Given a list of needed capabilities, returns the best matching packages with scores.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of capability IDs needed (e.g. ['pdf_extraction', 'web_search'])",
                        },
                        "framework": {"type": "string", "description": "Target framework (langchain, crewai, generic)"},
                        "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
                    },
                    "required": ["capabilities"],
                },
            ),
            Tool(
                name="agentnode_explain",
                description="Get detailed information about a specific AgentNode package including capabilities, permissions, trust info, and install instructions.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "package_slug": {"type": "string", "description": "Package slug (e.g. pdf-reader-pack)"},
                    },
                    "required": ["package_slug"],
                },
            ),
            Tool(
                name="agentnode_capabilities",
                description="List all available capability categories in the AgentNode taxonomy. Use this to discover what capability IDs exist.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Filter by category (e.g. document-processing, web-and-browsing)"},
                    },
                },
            ),
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "agentnode_search":
                body = {}
                if arguments.get("query"):
                    body["q"] = arguments["query"]
                if arguments.get("capability_id"):
                    body["capability_id"] = arguments["capability_id"]
                if arguments.get("framework"):
                    body["framework"] = arguments["framework"]
                if arguments.get("trust_level"):
                    body["trust_level"] = arguments["trust_level"]
                result = _api_request("POST", "/v1/search", body)

                # Format for readability
                hits = result.get("hits", [])
                formatted = []
                for h in hits[:10]:
                    formatted.append({
                        "slug": h.get("slug"),
                        "name": h.get("name"),
                        "summary": h.get("summary"),
                        "trust_level": h.get("trust_level"),
                        "frameworks": h.get("frameworks"),
                        "downloads": h.get("download_count"),
                        "install": f"agentnode install {h.get('slug')}",
                    })
                return [TextContent(
                    type="text",
                    text=json.dumps({"total": result.get("total", 0), "packages": formatted}, indent=2),
                )]

            elif name == "agentnode_resolve":
                body = {
                    "capabilities": arguments["capabilities"],
                    "limit": arguments.get("limit", 5),
                }
                if arguments.get("framework"):
                    body["framework"] = arguments["framework"]
                result = _api_request("POST", "/v1/resolve", body)
                return [TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str),
                )]

            elif name == "agentnode_explain":
                slug = arguments["package_slug"]
                result = _api_request("GET", f"/v1/packages/{slug}")
                return [TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str),
                )]

            elif name == "agentnode_capabilities":
                params = ""
                if arguments.get("category"):
                    params = f"?category={arguments['category']}"
                result = _api_request("GET", f"/v1/capabilities{params}")
                return [TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str),
                )]

            else:
                return [TextContent(
                    type="text",
                    text=json.dumps({"error": f"Unknown tool: {name}"}),
                )]

        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"error": str(e)}),
            )]

    return app


async def run_platform_server(api_url: str, api_key: str | None = None):
    """Run the platform MCP server over stdio."""
    app = create_platform_server(api_url, api_key)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(
        description="AgentNode Platform MCP Server — expose AgentNode registry as MCP tools"
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=os.environ.get("AGENTNODE_API_URL", DEFAULT_API_URL),
        help=f"AgentNode API URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("AGENTNODE_API_KEY"),
        help="AgentNode API key (optional, for authenticated endpoints)",
    )
    args = parser.parse_args()

    print(f"Starting AgentNode Platform MCP server (API: {args.api_url})", file=sys.stderr)

    import asyncio
    asyncio.run(run_platform_server(args.api_url, args.api_key))


if __name__ == "__main__":
    main()
