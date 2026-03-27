"""LangChain tool extractor — handles @tool decorator and BaseTool subclasses."""
from __future__ import annotations

import ast

from app.import_.converters.base import (
    LANGCHAIN_MODULES,
    ExtractedTool,
    ExtractResult,
    apply_return_policy,
    collect_business_imports,
    collect_helpers,
    detect_self_references,
    extract_full_node_source,
    extract_function_body,
    extract_params,
    get_return_annotation,
    parse_source,
    to_snake,
)


FRAMEWORK_IMPORT_SET = LANGCHAIN_MODULES


def extract(source: str, tree: ast.Module) -> ExtractResult:
    """Extract tools from LangChain source code.

    Detects two patterns:
    - Pattern A: @tool decorator on a function
    - Pattern B: BaseTool subclass with _run() method
    """
    result = ExtractResult()
    source_lines = source.splitlines()
    tool_func_names: set[str] = set()
    framework_class_names: set[str] = set()

    # Pattern A: @tool decorated functions
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _has_tool_decorator(node):
                tool = _extract_decorated_tool(node, source_lines, result)
                if tool:
                    tool_func_names.add(node.name)
                    result.tools.append(tool)
                    result.changes.append(f'Decorator `@tool` removed from `{node.name}`')

    # Pattern B: BaseTool subclasses
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if _is_basetool_subclass(node):
                framework_class_names.add(node.name)
                tool = _extract_basetool(node, source_lines, result)
                if tool:
                    tool_func_names.add(tool.name)
                    result.tools.append(tool)
                    result.changes.append(
                        f'BaseTool class `{node.name}` extracted into standalone function `{tool.name}()`'
                    )

    # Collect helpers (non-tool, non-framework nodes)
    helper_sources, helper_names = collect_helpers(
        tree, source_lines, tool_func_names, framework_class_names
    )
    result.helpers = helper_sources
    result.helper_names = helper_names

    return result


def _has_tool_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has a @tool decorator from langchain."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "tool":
            return True
        if isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name) and dec.func.id == "tool":
                return True
            if isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool":
                return True
    return False


def _extract_decorated_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    result: ExtractResult,
) -> ExtractedTool | None:
    """Extract a @tool decorated function."""
    is_async = isinstance(node, ast.AsyncFunctionDef)
    if is_async:
        result.warnings.append(
            f"Function `{node.name}` uses async/await. "
            "AgentNode can currently only run synchronous tools. "
            "Remove `async`/`await` and use a synchronous alternative "
            "(e.g. `requests` instead of `aiohttp`). **Not draft-ready.**"
        )

    name = node.name
    original_name = name
    description = ast.get_docstring(node) or ""

    params, param_warnings = extract_params(node)
    result.warnings.extend(param_warnings)

    body = extract_function_body(node, source_lines)
    return_annotation = get_return_annotation(node)

    body, has_return_dict, return_kind = apply_return_policy(
        name, return_annotation, body, result,
    )

    return ExtractedTool(
        name=name,
        original_name=original_name,
        description=description.split("\n")[0] if description else "",
        params=params,
        body_source=body,
        is_async=is_async,
        has_return_dict=has_return_dict,
        return_annotation=return_annotation,
        return_kind=return_kind.value,
    )


def _is_basetool_subclass(node: ast.ClassDef) -> bool:
    """Check if a class directly subclasses BaseTool."""
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseTool":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseTool":
            return True
    return False


def _extract_basetool(
    node: ast.ClassDef,
    source_lines: list[str],
    result: ExtractResult,
) -> ExtractedTool | None:
    """Extract a BaseTool subclass into a standalone function."""
    # Extract name and description from class body
    class_name = node.name
    tool_name = ""
    tool_description = ""

    for item in node.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    if target.id == "name" and isinstance(item.value, ast.Constant):
                        tool_name = str(item.value.value)
                    elif target.id == "description" and isinstance(item.value, ast.Constant):
                        tool_description = str(item.value.value)
        elif isinstance(item, ast.AnnAssign):
            if isinstance(item.target, ast.Name):
                if item.target.id == "name" and item.value and isinstance(item.value, ast.Constant):
                    tool_name = str(item.value.value)
                elif item.target.id == "description" and item.value and isinstance(item.value, ast.Constant):
                    tool_description = str(item.value.value)

    if not tool_name:
        tool_name = to_snake(class_name)

    func_name = to_snake(tool_name)

    # Find _run method
    run_method = None
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "_run":
            run_method = item
            break

    if not run_method:
        result.warnings.append(
            f"BaseTool `{class_name}` has no `_run()` method. Cannot extract."
        )
        return None

    is_async = isinstance(run_method, ast.AsyncFunctionDef)
    if is_async:
        result.warnings.append(
            f"Function `{func_name}` (from `{class_name}._run`) uses async/await. "
            "AgentNode can currently only run synchronous tools. "
            "Remove `async`/`await` and use a synchronous alternative. **Not draft-ready.**"
        )

    # Extract params (skip self)
    params, param_warnings = extract_params(run_method)
    result.warnings.extend(param_warnings)
    result.changes.append(f"`self` parameter removed from `{class_name}._run()`")

    # Extract body
    body = extract_function_body(run_method, source_lines)

    # Check for self references
    self_refs = detect_self_references(body)
    has_self_refs = bool(self_refs)
    if has_self_refs:
        for ref in self_refs[:3]:
            result.warnings.append(
                f"Code references `{ref}`. In ANP there is no `self` — "
                "use a function parameter instead. **Not draft-ready.**"
            )

    # Return type handling
    return_annotation = get_return_annotation(run_method)

    body, has_return_dict, return_kind = apply_return_policy(
        func_name, return_annotation, body, result,
    )

    # Check for args_schema
    for item in node.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id == "args_schema":
                    result.warnings.append(
                        f"`args_schema = {ast.unparse(item.value)}` was ignored. "
                        "Parameters extracted from `_run()` signature."
                    )
                    result.changes.append(
                        f"`args_schema` from `{class_name}` was ignored"
                    )
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if item.target.id == "args_schema":
                val = ast.unparse(item.value) if item.value else "..."
                result.warnings.append(
                    f"`args_schema = {val}` was ignored. "
                    "Parameters extracted from `_run()` signature."
                )

    # Docstring — prefer class docstring, then _run docstring
    description = tool_description or ast.get_docstring(run_method) or ast.get_docstring(node) or ""

    return ExtractedTool(
        name=func_name,
        original_name=tool_name,
        description=description.split("\n")[0] if description else "",
        params=params,
        body_source=body,
        is_async=is_async,
        has_return_dict=has_return_dict,
        return_annotation=return_annotation,
        return_kind=return_kind.value,
        has_self_refs=has_self_refs,
    )
