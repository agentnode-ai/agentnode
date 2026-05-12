"""AgentNode MCP Server — expose skills as MCP prompts and resources.

Thin adapter: maps MCP protocol primitives to AgentNode runtime.
Stateless — reads lockfile and filesystem on every request.
"""
from __future__ import annotations

import re

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError

from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.runtime import _resolve_under, _ASSET_MIME

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

mcp_app = FastMCP("agentnode")


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
        raise McpError(
            types.ErrorData(code=-32602, message=f"Invalid prompt name: {namespaced_name}. Expected format: slug/prompt-name")
        )

    slug, prompt_name = namespaced_name.split("/", 1)
    if not slug or not prompt_name:
        raise McpError(
            types.ErrorData(code=-32602, message=f"Invalid prompt name: {namespaced_name}. Expected format: slug/prompt-name")
        )

    lockfile = read_lockfile()
    pkg = lockfile.get("packages", {}).get(slug)
    if not pkg:
        raise McpError(
            types.ErrorData(code=-32602, message=f"Skill '{slug}' is not installed")
        )
    if pkg.get("package_type") != "skill":
        raise McpError(
            types.ErrorData(code=-32602, message=f"Package '{slug}' is not a skill")
        )

    from agentnode_sdk.skill import load_skill
    try:
        ctx = load_skill(slug)
    except Exception as exc:
        raise McpError(
            types.ErrorData(code=-32603, message=f"Failed to load skill '{slug}': {exc}")
        )

    if prompt_name not in ctx.prompts:
        available = ", ".join(ctx.prompts)
        raise McpError(
            types.ErrorData(code=-32602, message=f"Prompt '{prompt_name}' not found in '{slug}'. Available: {available}")
        )

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
        raise McpError(
            types.ErrorData(code=-32602, message=f"Resource not found: {uri}")
        )
    return content


# Register handlers on the low-level server

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
