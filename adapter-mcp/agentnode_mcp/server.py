"""MCP server that exposes installed AgentNode packs as MCP tools.

This allows any MCP-compatible client (OpenClaw, Claude Code, Cursor, etc.)
to use AgentNode packs as tools.

Usage:
    # Start the MCP server with specific packs
    agentnode-mcp --packs regex-builder-pack,json-processor-pack

    # Start with all installed packs (reads agentnode.lock)
    agentnode-mcp --all

    # In OpenClaw config or claude_desktop_config.json:
    {
        "mcpServers": {
            "agentnode": {
                "command": "agentnode-mcp",
                "args": ["--packs", "regex-builder-pack,document-redaction-pack"]
            }
        }
    }
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import logging
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

LOCKFILE = "agentnode.lock"


def _load_module(pack_slug: str) -> Any:
    """Import a pack's tool module."""
    module_name = pack_slug.replace("-", "_") + ".tool"
    return importlib.import_module(module_name)


def _get_run_params(func) -> dict:
    """Extract parameter info from a run() function for MCP tool schema."""
    sig = inspect.signature(func)
    properties = {}
    required = []

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        annotation = param.annotation
        json_type = "string"  # default

        if annotation != inspect.Parameter.empty:
            # Handle Optional types (e.g., str | None)
            origin = getattr(annotation, "__origin__", None)
            if origin is not None:
                args = getattr(annotation, "__args__", ())
                for arg in args:
                    if arg is not type(None):
                        json_type = type_map.get(arg, "string")
                        break
            else:
                json_type = type_map.get(annotation, "string")

        prop: dict[str, Any] = {"type": json_type}

        # Add default value as description hint
        if param.default != inspect.Parameter.empty:
            if param.default is not None:
                prop["default"] = param.default
        else:
            required.append(name)

        properties[name] = prop

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    return schema


def _get_packs_from_lock() -> list[str]:
    """Read installed packs from agentnode.lock."""
    if not os.path.exists(LOCKFILE):
        return []

    try:
        with open(LOCKFILE) as f:
            lock = json.load(f)
        return [entry["slug"] for entry in lock.get("packages", [])]
    except (json.JSONDecodeError, KeyError):
        return []


def _coerce_value(value: Any, target_type: str) -> Any:
    """Coerce a JSON value to the expected Python type."""
    if target_type == "integer" and isinstance(value, str):
        return int(value)
    if target_type == "number" and isinstance(value, str):
        return float(value)
    if target_type == "boolean" and isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return value


def create_server(pack_slugs: list[str]) -> Server:
    """Create an MCP server with tools from the specified packs."""
    app = Server("agentnode")

    # Load all pack modules
    loaded_packs: dict[str, Any] = {}
    tool_schemas: dict[str, dict] = {}

    for slug in pack_slugs:
        try:
            module = _load_module(slug)
            if not hasattr(module, "run"):
                logger.warning(f"Pack {slug} has no run() function, skipping")
                continue
            loaded_packs[slug] = module
            tool_schemas[slug] = _get_run_params(module.run)
            logger.info(f"Loaded pack: {slug}")
        except ImportError as e:
            logger.warning(f"Could not import {slug}: {e}")
        except Exception as e:
            logger.error(f"Error loading {slug}: {e}")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        tools = []
        for slug, module in loaded_packs.items():
            doc = getattr(module.run, "__doc__", "") or f"Tool from AgentNode pack: {slug}"
            # Use first line of docstring
            description = doc.strip().split("\n")[0]

            tools.append(Tool(
                name=slug,
                description=description,
                inputSchema=tool_schemas.get(slug, {"type": "object", "properties": {}}),
            ))
        return tools

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name not in loaded_packs:
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Unknown tool: {name}. Available: {list(loaded_packs.keys())}"}),
            )]

        module = loaded_packs[name]
        schema = tool_schemas.get(name, {})

        # Coerce argument types based on schema
        coerced_args = {}
        props = schema.get("properties", {})
        for key, value in arguments.items():
            if key in props:
                coerced_args[key] = _coerce_value(value, props[key].get("type", "string"))
            else:
                coerced_args[key] = value

        try:
            result = module.run(**coerced_args)
            return [TextContent(
                type="text",
                text=json.dumps(result, default=str, ensure_ascii=False),
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"error": str(e), "tool": name}),
            )]

    return app


async def run_server(pack_slugs: list[str]):
    """Run the MCP server over stdio."""
    app = create_server(pack_slugs)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(
        description="AgentNode MCP Server — expose AgentNode packs as MCP tools"
    )
    parser.add_argument(
        "--packs",
        type=str,
        default="",
        help="Comma-separated list of pack slugs to expose (e.g. regex-builder-pack,json-processor-pack)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Load all packs from agentnode.lock",
    )
    args = parser.parse_args()

    if args.all:
        pack_slugs = _get_packs_from_lock()
        if not pack_slugs:
            print("No packs found in agentnode.lock. Install packs first: agentnode install <pack>", file=sys.stderr)
            sys.exit(1)
    elif args.packs:
        pack_slugs = [s.strip() for s in args.packs.split(",") if s.strip()]
    else:
        print("Specify packs with --packs or use --all to load from agentnode.lock", file=sys.stderr)
        sys.exit(1)

    print(f"Starting AgentNode MCP server with {len(pack_slugs)} packs: {', '.join(pack_slugs)}", file=sys.stderr)

    import asyncio
    asyncio.run(run_server(pack_slugs))


if __name__ == "__main__":
    main()
