"""Generate API documentation from Python source code."""

from __future__ import annotations

import ast
import textwrap


def run(code: str, format: str = "markdown", title: str = "API Documentation") -> dict:
    """Generate documentation from Python source code.

    Args:
        code: Python source code to document.
        format: Output format - "markdown" or "openapi".
        title: Title for the documentation.

    Returns:
        dict with keys: documentation, endpoints, format.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {
            "documentation": f"Error parsing code: {exc}",
            "endpoints": 0,
            "format": format,
        }

    items = _extract_all(tree, code)
    endpoint_count = sum(1 for item in items if item["kind"] in ("function", "method"))

    if format == "openapi":
        doc = _generate_openapi(items, title)
    else:
        doc = _generate_markdown(items, title)

    return {
        "documentation": doc,
        "endpoints": endpoint_count,
        "format": format,
    }


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def _extract_all(tree: ast.Module, source: str) -> list[dict]:
    """Extract all documentable items from the module."""
    items: list[dict] = []

    # Module-level docstring
    module_doc = ast.get_docstring(tree) or ""
    if module_doc:
        items.append({"kind": "module", "name": "__module__", "docstring": module_doc, "params": [], "returns": "", "decorators": [], "bases": []})

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            items.append(_extract_class(node))
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    item = _extract_function(child)
                    item["kind"] = "method"
                    item["class_name"] = node.name
                    items.append(item)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append(_extract_function(node))

    return items


def _extract_class(node: ast.ClassDef) -> dict:
    """Extract class metadata."""
    bases = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            bases.append(base.id)
        elif isinstance(base, ast.Attribute):
            bases.append(ast.dump(base))

    return {
        "kind": "class",
        "name": node.name,
        "docstring": ast.get_docstring(node) or "",
        "params": [],
        "returns": "",
        "decorators": [_decorator_name(d) for d in node.decorator_list],
        "bases": bases,
        "line": node.lineno,
    }


def _extract_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    """Extract function/method metadata."""
    params: list[dict] = []
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        type_hint = _annotation_str(arg.annotation) if arg.annotation else ""
        params.append({"name": arg.arg, "type": type_hint})

    # Defaults
    num_defaults = len(node.args.defaults)
    if num_defaults:
        offset = len(params) - num_defaults
        for i, default in enumerate(node.args.defaults):
            idx = offset + i
            if 0 <= idx < len(params):
                params[idx]["default"] = _const_repr(default)

    return_type = _annotation_str(node.returns) if node.returns else ""

    return {
        "kind": "function",
        "name": node.name,
        "docstring": ast.get_docstring(node) or "",
        "params": params,
        "returns": return_type,
        "decorators": [_decorator_name(d) for d in node.decorator_list],
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "line": node.lineno,
        "bases": [],
    }


def _annotation_str(node: ast.expr | None) -> str:
    """Convert an annotation AST node to a readable string."""
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Attribute):
        return f"{_annotation_str(node.value)}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return f"{_annotation_str(node.value)}[{_annotation_str(node.slice)}]"
    if isinstance(node, ast.Tuple):
        return ", ".join(_annotation_str(e) for e in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return f"{_annotation_str(node.left)} | {_annotation_str(node.right)}"
    return ast.dump(node)


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_decorator_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ast.dump(node)


def _const_repr(node: ast.expr) -> str:
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    return "..."


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def _generate_markdown(items: list[dict], title: str) -> str:
    """Generate Markdown documentation."""
    lines: list[str] = [f"# {title}", ""]

    # Module docstring
    for item in items:
        if item["kind"] == "module":
            lines.append(item["docstring"])
            lines.append("")
            break

    # Table of contents
    classes = [i for i in items if i["kind"] == "class"]
    functions = [i for i in items if i["kind"] == "function"]
    methods = [i for i in items if i["kind"] == "method"]

    if classes or functions:
        lines.append("## Table of Contents")
        lines.append("")
        for cls in classes:
            lines.append(f"- [{cls['name']}](#{cls['name'].lower()})")
        for func in functions:
            lines.append(f"- [{func['name']}()](#{func['name'].lower()})")
        lines.append("")

    # Classes
    for cls in classes:
        lines.append(f"## {cls['name']}")
        lines.append("")
        if cls.get("bases"):
            lines.append(f"**Bases:** {', '.join(cls['bases'])}")
            lines.append("")
        if cls.get("decorators"):
            lines.append(f"**Decorators:** {', '.join('@' + d for d in cls['decorators'])}")
            lines.append("")
        if cls["docstring"]:
            lines.append(cls["docstring"])
            lines.append("")

        # Methods of this class
        cls_methods = [m for m in methods if m.get("class_name") == cls["name"]]
        for method in cls_methods:
            lines.extend(_format_function_md(method, heading_level=3))

    # Top-level functions
    if functions:
        lines.append("## Functions")
        lines.append("")
        for func in functions:
            lines.extend(_format_function_md(func, heading_level=3))

    return "\n".join(lines)


def _format_function_md(func: dict, heading_level: int = 3) -> list[str]:
    """Format a single function as Markdown."""
    prefix = "#" * heading_level
    async_tag = " *(async)*" if func.get("is_async") else ""
    lines = [f"{prefix} `{func['name']}`{async_tag}", ""]

    if func.get("decorators"):
        lines.append(f"**Decorators:** {', '.join('@' + d for d in func['decorators'])}")
        lines.append("")

    # Signature
    sig_parts: list[str] = []
    for p in func["params"]:
        part = p["name"]
        if p.get("type"):
            part += f": {p['type']}"
        if p.get("default"):
            part += f" = {p['default']}"
        sig_parts.append(part)
    ret = f" -> {func['returns']}" if func.get("returns") else ""
    lines.append(f"```python")
    lines.append(f"def {func['name']}({', '.join(sig_parts)}){ret}")
    lines.append(f"```")
    lines.append("")

    if func["docstring"]:
        lines.append(func["docstring"])
        lines.append("")

    # Parameters table
    if func["params"]:
        lines.append("| Parameter | Type | Default | Description |")
        lines.append("|-----------|------|---------|-------------|")
        for p in func["params"]:
            ptype = p.get("type", "-")
            default = p.get("default", "-")
            lines.append(f"| `{p['name']}` | `{ptype or '-'}` | `{default}` | - |")
        lines.append("")

    if func.get("returns"):
        lines.append(f"**Returns:** `{func['returns']}`")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# OpenAPI-style generation
# ---------------------------------------------------------------------------

def _generate_openapi(items: list[dict], title: str) -> str:
    """Generate an OpenAPI-style YAML documentation string."""
    import json

    functions = [i for i in items if i["kind"] in ("function", "method")]

    spec: dict = {
        "openapi": "3.0.0",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {},
    }

    for func in functions:
        path = f"/{func['name']}"
        parameters = []
        for p in func["params"]:
            param = {
                "name": p["name"],
                "in": "query",
                "required": "default" not in p,
                "schema": {"type": _python_type_to_json(p.get("type", "string"))},
            }
            if "default" in p:
                param["schema"]["default"] = p["default"]
            parameters.append(param)

        endpoint: dict = {
            "post": {
                "summary": func["docstring"].split("\n")[0] if func["docstring"] else func["name"],
                "description": func["docstring"],
                "parameters": parameters,
                "responses": {
                    "200": {
                        "description": "Successful response",
                    }
                },
            }
        }

        if func.get("returns"):
            endpoint["post"]["responses"]["200"]["content"] = {
                "application/json": {
                    "schema": {"type": _python_type_to_json(func["returns"])}
                }
            }

        spec["paths"][path] = endpoint

    return json.dumps(spec, indent=2)


def _python_type_to_json(py_type: str) -> str:
    """Map Python type hints to JSON schema types."""
    mapping = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "list": "array",
        "dict": "object",
        "None": "null",
    }
    return mapping.get(py_type, "string")
