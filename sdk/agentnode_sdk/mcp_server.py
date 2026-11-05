"""AgentNode MCP Server — expose skills as MCP prompts and resources.

Thin adapter: maps MCP protocol primitives to AgentNode runtime.
Stateless — reads lockfile and filesystem on every request.
"""
from __future__ import annotations

import re

import mcp.types as types

from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.runtime import _resolve_under, _ASSET_MIME

# mcp 1.x and 2.x differ where this module touches them: the server class (FastMCP in
# 1.x, removed in 2.x) and the error type (McpError(ErrorData) vs MCPError(code, message)).
# Everything below the binding -- prompt/resource building -- is the same for both.
try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # mcp 2.x
    FastMCP = None

MCP_V1 = FastMCP is not None

if MCP_V1:
    from mcp.shared.exceptions import McpError
else:
    from mcp.shared.exceptions import MCPError as McpError


def _mcp_error(code: int, message: str) -> Exception:
    if MCP_V1:
        return McpError(types.ErrorData(code=code, message=message))
    return McpError(code, message)


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _build_prompt_list() -> list[types.Prompt]:
    """Build namespaced prompt list from all installed skills."""
    from pathlib import Path

    lockfile = read_lockfile()
    packages = lockfile.get("packages", {})
    prompts: list[types.Prompt] = []

    for slug, info in packages.items():
        if info.get("package_type") != "skill":
            continue

        install_path = info.get("install_path", "")

        for p in info.get("prompts", []):
            name = p.get("name")
            template = p.get("template")
            if not name or not template:
                continue

            namespaced = f"{slug}/{name}"

            # Extract arguments from manifest or infer from {{placeholders}}
            args: list[types.PromptArgument] = []
            manifest_args = p.get("arguments")
            if manifest_args and isinstance(manifest_args, list):
                for a in manifest_args:
                    if isinstance(a, dict) and a.get("name"):
                        args.append(types.PromptArgument(
                            name=a["name"],
                            description=a.get("description"),
                            required=a.get("required", False),
                        ))
            elif install_path:
                safe = _resolve_under(Path(install_path), template)
                if safe and safe.is_file():
                    try:
                        content = safe.read_text(encoding="utf-8")
                        placeholders = list(dict.fromkeys(_PLACEHOLDER_RE.findall(content)))
                        for var in placeholders:
                            args.append(types.PromptArgument(
                                name=var,
                                description=None,
                                required=False,
                            ))
                    except Exception:
                        pass

            prompts.append(types.Prompt(
                name=namespaced,
                description=p.get("description"),
                arguments=args or None,
            ))

    return prompts


def _render_prompt(namespaced_name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
    """Render a skill prompt by namespaced name."""
    if "/" not in namespaced_name:
        raise _mcp_error(-32602, f"Invalid prompt name: {namespaced_name}. Expected format: slug/prompt-name")

    slug, prompt_name = namespaced_name.split("/", 1)
    if not slug or not prompt_name:
        raise _mcp_error(-32602, f"Invalid prompt name: {namespaced_name}. Expected format: slug/prompt-name")

    lockfile = read_lockfile()
    pkg = lockfile.get("packages", {}).get(slug)
    if not pkg:
        raise _mcp_error(-32602, f"Skill '{slug}' is not installed")
    if pkg.get("package_type") != "skill":
        raise _mcp_error(-32602, f"Package '{slug}' is not a skill")

    from agentnode_sdk.skill import load_skill
    try:
        ctx = load_skill(slug)
    except Exception as exc:
        raise _mcp_error(-32603, f"Failed to load skill '{slug}': {exc}")

    if prompt_name not in ctx.prompts:
        available = ", ".join(ctx.prompts)
        raise _mcp_error(-32602, f"Prompt '{prompt_name}' not found in '{slug}'. Available: {available}")

    text = ctx.render_prompt(prompt_name, **(arguments or {}))

    return types.GetPromptResult(
        description=f"{slug}/{prompt_name}",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=text),
            )
        ],
    )


def _build_resource_list() -> list[types.Resource]:
    """Build resource list from all installed skill assets."""
    lockfile = read_lockfile()
    packages = lockfile.get("packages", {})
    resources: list[types.Resource] = []

    for slug, info in packages.items():
        if info.get("package_type") != "skill":
            continue

        for a in info.get("assets", []):
            aid = a.get("id")
            if not aid:
                continue
            atype = a.get("type", "document")
            resources.append(types.Resource(
                name=aid,
                uri=f"agentnode://skills/{slug}/assets/{aid}",
                description=a.get("description"),
                mimeType=_ASSET_MIME.get(atype, "text/plain"),
            ))

    return resources


def _read_resource(uri: str) -> str:
    """Read resource content by URI."""
    from agentnode_sdk.runtime import AgentNodeRuntime
    rt = AgentNodeRuntime.__new__(AgentNodeRuntime)
    rt._minimum_trust_level = "verified"
    content = rt.read_resource(str(uri))
    if content is None:
        raise _mcp_error(-32602, f"Resource not found: {uri}")
    return content


# Tools: installed pack tools (run through runner.run_tool) and the platform tools.
# `agentnode serve --packs a,b` limits the pack tools, like `agentnode-mcp --packs`.

_PACKS: set[str] | None = None


def _tool_list() -> list[types.Tool]:
    from agentnode_sdk import mcp_tools

    return [types.Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
            for t in mcp_tools.list_tools(_PACKS)]


async def _run_tool(name: str, arguments: dict | None) -> types.CallToolResult:
    import asyncio

    from agentnode_sdk import mcp_tools

    try:
        out = await asyncio.to_thread(mcp_tools.call_tool, name, arguments, _PACKS)
    except mcp_tools.UnknownTool:
        raise _mcp_error(-32602, f"Unknown tool: {name}")
    return types.CallToolResult(content=[types.TextContent(type="text", text=out.text)],
                                isError=out.is_error)


# Bind the handlers to the installed mcp's server.

if MCP_V1:
    mcp_app = FastMCP("agentnode")

    @mcp_app._mcp_server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return _tool_list()

    # No schema validation here: arguments are coerced like adapter-mcp did, and run_tool
    # validates the tool input itself.
    @mcp_app._mcp_server.call_tool(validate_input=False)
    async def handle_call_tool(name: str, arguments: dict) -> types.CallToolResult:
        return await _run_tool(name, arguments)

    @mcp_app._mcp_server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return _build_prompt_list()

    @mcp_app._mcp_server.get_prompt()
    async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        return _render_prompt(name, arguments)

    @mcp_app._mcp_server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return _build_resource_list()

    @mcp_app._mcp_server.read_resource()
    async def handle_read_resource(uri) -> str:
        return _read_resource(str(uri))

else:
    from mcp.server.lowlevel import Server

    async def _on_list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=_build_prompt_list())

    async def _on_get_prompt(ctx, params) -> types.GetPromptResult:
        return _render_prompt(params.name, params.arguments)

    async def _on_list_resources(ctx, params) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=_build_resource_list())

    async def _on_read_resource(ctx, params) -> types.ReadResourceResult:
        uri = str(params.uri)
        return types.ReadResourceResult(contents=[
            types.TextResourceContents(uri=uri, mimeType="text/plain", text=_read_resource(uri)),
        ])

    async def _on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_tool_list())

    async def _on_call_tool(ctx, params) -> types.CallToolResult:
        return await _run_tool(params.name, params.arguments)

    mcp_app = Server(
        "agentnode",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
        on_list_prompts=_on_list_prompts,
        on_get_prompt=_on_get_prompt,
        on_list_resources=_on_list_resources,
        on_read_resource=_on_read_resource,
    )


async def run_stdio() -> None:
    """Serve installed skills (prompts/resources) and tools over stdio with whichever mcp major
    version is installed. stdin is the protocol channel: nothing may prompt on it."""
    import os

    os.environ["AGENTNODE_NON_INTERACTIVE"] = "1"
    if MCP_V1:
        await mcp_app.run_stdio_async()
        return
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await mcp_app.run(read_stream, write_stream, mcp_app.create_initialization_options())
