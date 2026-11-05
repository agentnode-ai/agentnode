"""Tools that ``agentnode serve`` offers over MCP: installed pack tools and the platform tools.

This replaces the tool side of the separate ``adapter-mcp`` package. The names and the four
platform tools keep that package's contracts, so an MCP client configuration only has to swap
the command. What changes is how a pack tool runs: never by importing the pack into this
process, always through :func:`agentnode_sdk.runner.run_tool` -- the same lock-integrity,
policy, guard, host-trust and sandbox gates as ``agentnode run``. A failure or a refusal is an
MCP tool error (``isError``), not an answer.

stdin is the MCP channel while serving, so nothing here may ever ask on it: every call runs
non-interactive, and anything that would need a confirmation is refused.

Version-neutral: no ``mcp`` import. :mod:`agentnode_sdk.mcp_server` binds this to mcp 1.x/2.x.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from agentnode_sdk.installer import read_lockfile

PLATFORM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "agentnode_search",
        "description": "Search the AgentNode package registry for AI agent capabilities. Returns packages matching the query with trust levels and compatibility info.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (name, capability, or keyword)"},
                "capability_id": {"type": "string", "description": "Filter by specific capability ID (e.g. pdf_extraction, web_search)"},
                "framework": {"type": "string", "description": "Filter by framework (langchain, crewai, generic)"},
                "trust_level": {"type": "string", "description": "Minimum trust level (unverified, verified, trusted, curated)"},
            },
            "required": [],
        },
    },
    {
        "name": "agentnode_resolve",
        "description": "Resolve capability gaps to ranked package recommendations. Given a list of needed capabilities, returns the best matching packages with scores.",
        "inputSchema": {
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
    },
    {
        "name": "agentnode_explain",
        "description": "Get detailed information about a specific AgentNode package including capabilities, permissions, trust info, and install instructions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package_slug": {"type": "string", "description": "Package slug (e.g. pdf-reader-pack)"},
            },
            "required": ["package_slug"],
        },
    },
    {
        "name": "agentnode_capabilities",
        "description": "List all available capability categories in the AgentNode taxonomy. Use this to discover what capability IDs exist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by category (e.g. document-processing, web-and-browsing)"},
            },
        },
    },
]
_PLATFORM_NAMES = {t["name"] for t in PLATFORM_TOOLS}
_EMPTY_SCHEMA = {"type": "object", "properties": {}}


class UnknownTool(LookupError):
    """No installed pack tool and no platform tool has this name."""


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    is_error: bool


# ---------------------------------------------------------------------------- pack tools


def _pack_tools(packs: set[str] | None) -> dict[str, dict[str, Any]]:
    """MCP name -> {slug, tool, description, schema}. Names as adapter-mcp gave them: the slug
    for a single-tool package, ``slug/tool`` when it has several. Skills stay prompts."""
    out: dict[str, dict[str, Any]] = {}
    for slug, entry in (read_lockfile().get("packages") or {}).items():
        if packs is not None and slug not in packs:
            continue
        if not isinstance(entry, dict) or entry.get("package_type") == "skill":
            continue
        tools = [t for t in (entry.get("tools") or []) if isinstance(t, dict) and t.get("name")]
        if not tools:
            if entry.get("entrypoint"):
                out[slug] = {"slug": slug, "tool": None, "schema": _EMPTY_SCHEMA,
                             "description": f"Tool from AgentNode package {slug}"}
            continue
        for t in tools:
            name = slug if len(tools) == 1 else f"{slug}/{t['name']}"
            schema = t.get("input_schema") or t.get("inputSchema") or _EMPTY_SCHEMA
            out[name] = {"slug": slug, "tool": t["name"], "schema": schema,
                         "description": t.get("description")
                         or f"Tool '{t['name']}' from AgentNode package {slug}"}
    return out


def _coerce(value: Any, target: str) -> Any:
    """String arguments to the schema's scalar type, as adapter-mcp did."""
    if not isinstance(value, str):
        return value
    try:
        if target == "integer":
            return int(value)
        if target == "number":
            return float(value)
    except ValueError:
        return value
    if target == "boolean":
        return value.lower() in ("true", "1", "yes")
    return value


def _call_pack(spec: dict[str, Any], arguments: dict[str, Any]) -> ToolOutcome:
    from agentnode_sdk import runner

    props = (spec["schema"] or {}).get("properties") or {}
    kwargs = {k: _coerce(v, (props.get(k) or {}).get("type", "")) for k, v in arguments.items()}
    res = runner.run_tool(spec["slug"], spec["tool"], **kwargs)
    if res.success:
        return ToolOutcome(json.dumps(res.result, default=str, ensure_ascii=False), False)
    return ToolOutcome(json.dumps({"error": res.error or "tool run failed",
                                   "tool": spec["tool"] or spec["slug"]}, ensure_ascii=False), True)


# ---------------------------------------------------------------------------- platform tools


def _http_client(**kwargs) -> httpx.Client:
    return httpx.Client(**kwargs)


def _api(method: str, path: str, body: dict | None = None) -> Any:
    from agentnode_sdk.client import DEFAULT_BASE_URL

    headers = {"Content-Type": "application/json"}
    key = os.environ.get("AGENTNODE_API_KEY")
    if key:
        headers["X-API-Key"] = key
    base = os.environ.get("AGENTNODE_API_URL", DEFAULT_BASE_URL).rstrip("/")
    with _http_client(base_url=base, headers=headers, timeout=30) as client:
        resp = client.get(path) if method == "GET" else client.post(path, json=body or {})
        resp.raise_for_status()
        return resp.json()


def _call_platform(name: str, a: dict[str, Any]) -> ToolOutcome:
    if name == "agentnode_search":
        body = {k_out: a[k_in] for k_in, k_out in (("query", "q"), ("capability_id", "capability_id"),
                                                   ("framework", "framework"),
                                                   ("trust_level", "trust_level")) if a.get(k_in)}
        result = _api("POST", "/v1/search", body)
        packages = [{
            "slug": h.get("slug"), "name": h.get("name"), "summary": h.get("summary"),
            "trust_level": h.get("trust_level"), "frameworks": h.get("frameworks"),
            "downloads": h.get("download_count"), "install": f"agentnode install {h.get('slug')}",
        } for h in (result.get("hits") or [])[:10]]
        return ToolOutcome(json.dumps({"total": result.get("total", 0), "packages": packages},
                                      indent=2), False)
    if name == "agentnode_resolve":
        body = {"capabilities": a["capabilities"], "limit": a.get("limit", 5)}
        if a.get("framework"):
            body["framework"] = a["framework"]
        result = _api("POST", "/v1/resolve", body)
    elif name == "agentnode_explain":
        result = _api("GET", f"/v1/packages/{a['package_slug']}")
    else:  # agentnode_capabilities
        query = f"?category={a['category']}" if a.get("category") else ""
        result = _api("GET", f"/v1/capabilities{query}")
    return ToolOutcome(json.dumps(result, indent=2, default=str), False)


# ---------------------------------------------------------------------------- public surface


def list_tools(packs: set[str] | None = None) -> list[dict[str, Any]]:
    tools = [{"name": n, "description": s["description"], "inputSchema": s["schema"]}
             for n, s in sorted(_pack_tools(packs).items())]
    return tools + [dict(t) for t in PLATFORM_TOOLS]


def call_tool(name: str, arguments: dict[str, Any] | None,
              packs: set[str] | None = None) -> ToolOutcome:
    """Run one tool. Raises :class:`UnknownTool` for a name nobody offers.

    No confirmation callback is ever passed to run_tool, so a tool that needs one is refused
    rather than asked; `run_stdio` also sets AGENTNODE_NON_INTERACTIVE for the serve process.
    """
    arguments = dict(arguments or {})
    if name in _PLATFORM_NAMES:
        try:
            return _call_platform(name, arguments)
        except Exception as exc:  # noqa: BLE001 - reported to the client as a tool error
            return ToolOutcome(json.dumps({"error": str(exc)}), True)
    spec = _pack_tools(packs).get(name)
    if spec is None:
        raise UnknownTool(name)
    try:
        return _call_pack(spec, arguments)
    except Exception as exc:  # noqa: BLE001 - a crash in the run path is a tool error too
        return ToolOutcome(json.dumps({"error": f"{type(exc).__name__}: {exc}",
                                       "tool": spec["tool"] or spec["slug"]}), True)
