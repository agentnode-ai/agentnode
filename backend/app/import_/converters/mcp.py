"""MCP tool extractor — handles FastMCP @mcp.tool() and low-level Server patterns."""
from __future__ import annotations

import ast

from app.import_.converters.base import (
    MCP_MODULES,
    ExtractedTool,
    ExtractResult,
    apply_return_policy,
    collect_helpers,
    extract_full_node_source,
    extract_function_body,
    extract_params,
    get_return_annotation,
    to_snake,
)
from app.import_.schemas import ToolParam


FRAMEWORK_IMPORT_SET = MCP_MODULES

# Type annotations that indicate an MCP Context parameter (should be stripped)
_MCP_CONTEXT_TYPES = frozenset({
    "Context",
    "mcp.server.fastmcp.Context",
    "fastmcp.Context",
    "ServerContext",
})


def extract(source: str, tree: ast.Module) -> ExtractResult:
    """Extract tools from MCP server source code.

    Detects:
    - Pattern A: FastMCP @mcp.tool() decorator (primary — fully extracted)
    - Pattern B: Low-level Server @server.call_tool() dispatch (warn only)
    - Also detects @mcp.resource() and @mcp.prompt() (skipped with warning)
    """
    result = ExtractResult()
    source_lines = source.splitlines()
    tool_func_names: set[str] = set()
    framework_class_names: set[str] = set()

    # Discover FastMCP and Server variable names
    fastmcp_vars = _find_fastmcp_var_names(tree)
    server_vars = _find_server_var_names(tree)

    # Pattern A: @mcp.tool() decorated functions (FastMCP)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _has_mcp_tool_decorator(node, fastmcp_vars):
                tool = _extract_fastmcp_tool(node, source_lines, result, fastmcp_vars)
                if tool:
                    tool_func_names.add(node.name)
                    result.tools.append(tool)
                    dec_var = _get_decorator_var(node, fastmcp_vars)
                    result.changes.append(
                        f"Decorator `@{dec_var}.tool()` removed from `{node.name}`"
                    )

    # Pattern B: Low-level Server.call_tool() — warn only
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _has_call_tool_decorator(node, server_vars):
                result.warnings.append(
                    f"Low-level `Server.call_tool()` dispatch pattern found in `{node.name}`. "
                    "Please refactor each tool into a separate `@mcp.tool()` function "
                    "using FastMCP for proper extraction. **Not draft-ready.**"
                )

    # Detect @mcp.resource() and @mcp.prompt() — not extractable as tools
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = _is_resource_or_prompt(node, fastmcp_vars)
            if kind:
                result.warnings.append(
                    f"MCP {kind} `{node.name}` detected. Only tools can be "
                    f"converted to ANP packages. This {kind} was skipped."
                )

    # Collect helpers (non-tool, non-framework nodes)
    helper_sources, helper_names = collect_helpers(
        tree, source_lines, tool_func_names, framework_class_names
    )

    # Filter out FastMCP/Server instantiations and __main__ blocks from helpers
    all_instance_vars = fastmcp_vars | server_vars
    filtered_helpers = []
    filtered_names = set(helper_names)
    for h in helper_sources:
        # Skip FastMCP("...") or Server("...") assignments
        if any(
            f"{var}" in h and ("FastMCP(" in h or "Server(" in h)
            for var in all_instance_vars
        ):
            continue
        # Skip if __name__ == "__main__" blocks
        if "__name__" in h and "__main__" in h:
            continue
        # Skip bare .run() calls (mcp.run(), server.run(), asyncio.run(...))
        stripped = h.strip()
        if any(f"{var}.run(" in stripped for var in all_instance_vars):
            continue
        filtered_helpers.append(h)

    result.helpers = filtered_helpers
    result.helper_names = filtered_names - all_instance_vars

    return result


# ── FastMCP variable detection ──────────────────────────────────────


def _find_fastmcp_var_names(tree: ast.Module) -> set[str]:
    """Find variable names assigned to FastMCP instances.

    Matches: mcp = FastMCP("name"), server = FastMCP("name", ...)
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if _is_fastmcp_call(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _find_server_var_names(tree: ast.Module) -> set[str]:
    """Find variable names assigned to low-level Server instances."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if _is_server_call(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _is_fastmcp_call(call: ast.Call) -> bool:
    """Check if a Call node is FastMCP(...)."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "FastMCP":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "FastMCP":
        return True
    return False


def _is_server_call(call: ast.Call) -> bool:
    """Check if a Call node is Server(...) (low-level MCP)."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "Server":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "Server":
        return True
    return False


# ── Decorator detection ─────────────────────────────────────────────


def _has_mcp_tool_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    fastmcp_vars: set[str],
) -> bool:
    """Check if function has a @mcp.tool() or @mcp.tool decorator."""
    for dec in node.decorator_list:
        # @mcp.tool (bare attribute, no call)
        if isinstance(dec, ast.Attribute):
            if dec.attr == "tool" and isinstance(dec.value, ast.Name):
                if dec.value.id in fastmcp_vars:
                    return True
        # @mcp.tool() or @mcp.tool(description="...")
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Attribute) and func.attr == "tool":
                if isinstance(func.value, ast.Name) and func.value.id in fastmcp_vars:
                    return True
    return False


def _has_call_tool_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    server_vars: set[str],
) -> bool:
    """Check for @server.call_tool() decorator (low-level MCP pattern)."""
    for dec in node.decorator_list:
        attr_name = None
        if isinstance(dec, ast.Attribute):
            attr_name = dec.attr
            var_node = dec.value
        elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            attr_name = dec.func.attr
            var_node = dec.func.value
        else:
            continue

        if attr_name == "call_tool" and isinstance(var_node, ast.Name):
            if var_node.id in server_vars:
                return True
    return False


def _is_resource_or_prompt(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    fastmcp_vars: set[str],
) -> str | None:
    """Check if function has @mcp.resource() or @mcp.prompt() decorator.

    Returns "resource" or "prompt" or None.
    """
    for dec in node.decorator_list:
        for attr_name in ("resource", "prompt"):
            if isinstance(dec, ast.Attribute):
                if dec.attr == attr_name and isinstance(dec.value, ast.Name):
                    if dec.value.id in fastmcp_vars:
                        return attr_name
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr == attr_name and isinstance(dec.func.value, ast.Name):
                    if dec.func.value.id in fastmcp_vars:
                        return attr_name
    return None


def _get_decorator_var(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    fastmcp_vars: set[str],
) -> str:
    """Get the variable name used in the @var.tool() decorator."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
            if dec.value.id in fastmcp_vars:
                return dec.value.id
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if isinstance(dec.func.value, ast.Name) and dec.func.value.id in fastmcp_vars:
                return dec.func.value.id
    return "mcp"


# ── Decorator metadata extraction ───────────────────────────────────


def _get_decorator_metadata(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    fastmcp_vars: set[str],
) -> dict[str, str | None]:
    """Extract name and description from @mcp.tool(...) kwargs."""
    meta: dict[str, str | None] = {"name": None, "description": None}
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if (dec.func.attr == "tool"
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id in fastmcp_vars):
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        meta["name"] = str(kw.value.value)
                    elif kw.arg == "description" and isinstance(kw.value, ast.Constant):
                        meta["description"] = str(kw.value.value)
                break
    return meta


# ── Context parameter filtering ─────────────────────────────────────


def _filter_context_params(
    params: list[ToolParam],
    result: ExtractResult,
    func_name: str,
) -> list[ToolParam]:
    """Remove MCP Context parameters from the parameter list."""
    filtered = []
    for p in params:
        if p.type_hint in _MCP_CONTEXT_TYPES:
            result.changes.append(
                f"MCP `Context` parameter `{p.name}` removed from `{func_name}`"
            )
        else:
            filtered.append(p)
    return filtered


# ── Tool extraction ─────────────────────────────────────────────────


def _extract_fastmcp_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    result: ExtractResult,
    fastmcp_vars: set[str],
) -> ExtractedTool | None:
    """Extract a @mcp.tool() decorated function."""
    is_async = isinstance(node, ast.AsyncFunctionDef)
    if is_async:
        result.warnings.append(
            f"Function `{node.name}` uses async/await. "
            "AgentNode can currently only run synchronous tools. "
            "Remove `async`/`await` and use a synchronous alternative "
            "(e.g. `requests` instead of `httpx`). **Not draft-ready.**"
        )

    # Name: from decorator name= kwarg, or function name
    meta = _get_decorator_metadata(node, fastmcp_vars)
    original_name = meta["name"] or node.name
    func_name = to_snake(original_name) if meta["name"] else node.name

    # Description: decorator description= kwarg, or docstring
    description = meta["description"] or ast.get_docstring(node) or ""

    # Parameters: extract, then filter out Context
    params, param_warnings = extract_params(node)
    result.warnings.extend(param_warnings)
    params = _filter_context_params(params, result, func_name)

    # Body
    body = extract_function_body(node, source_lines)

    # Return type
    return_annotation = get_return_annotation(node)

    # Apply return policy (wrapping, warnings)
    body, has_return_dict, return_kind = apply_return_policy(
        func_name, return_annotation, body, result,
    )

    return ExtractedTool(
        name=func_name,
        original_name=original_name,
        description=description.split("\n")[0] if description else "",
        params=params,
        body_source=body,
        is_async=is_async,
        has_return_dict=has_return_dict,
        return_annotation=return_annotation,
        return_kind=return_kind.value,
    )
