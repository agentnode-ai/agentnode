"""CrewAI tool extractor — handles @tool("Name") decorator and BaseTool subclasses."""
from __future__ import annotations

import ast

from app.import_.converters.base import (
    CREWAI_MODULES,
    ExtractedTool,
    ExtractResult,
    apply_return_policy,
    collect_helpers,
    detect_self_references,
    extract_function_body,
    extract_params,
    get_return_annotation,
    to_snake,
)


FRAMEWORK_IMPORT_SET = CREWAI_MODULES


def extract(source: str, tree: ast.Module) -> ExtractResult:
    """Extract tools from CrewAI source code.

    Detects:
    - @tool("Name With Spaces") decorator
    - @tool (no arg) decorator
    - BaseTool subclass with Pydantic field syntax
    """
    result = ExtractResult()
    source_lines = source.splitlines()
    tool_func_names: set[str] = set()
    framework_class_names: set[str] = set()

    # @tool decorated functions
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorator_name = _get_tool_decorator_name(node)
            if decorator_name is not None:
                tool = _extract_decorated_tool(node, decorator_name, source_lines, result)
                if tool:
                    tool_func_names.add(node.name)
                    result.tools.append(tool)
                    result.changes.append(f'Decorator `@tool` removed from `{node.name}`')

    # BaseTool subclasses
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

    # Collect helpers
    helper_sources, helper_names = collect_helpers(
        tree, source_lines, tool_func_names, framework_class_names
    )
    result.helpers = helper_sources
    result.helper_names = helper_names

    return result


def _get_tool_decorator_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Get the tool name from @tool decorator, or None if not a tool.

    Returns:
        - The string argument if @tool("Name With Spaces")
        - Empty string if @tool (no args) — means use function name
        - None if no @tool decorator
    """
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "tool":
            return ""  # @tool without args
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name) and func.id == "tool":
                # @tool("Name") or @tool()
                if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    return dec.args[0].value
                return ""
            if isinstance(func, ast.Attribute) and func.attr == "tool":
                if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    return dec.args[0].value
                return ""
    return None


def _extract_decorated_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator_name: str,
    source_lines: list[str],
    result: ExtractResult,
) -> ExtractedTool | None:
    """Extract a @tool decorated function."""
    is_async = isinstance(node, ast.AsyncFunctionDef)
    if is_async:
        result.warnings.append(
            f"Function `{node.name}` uses async/await. "
            "AgentNode can currently only run synchronous tools. "
            "Remove `async`/`await` and use a synchronous alternative. **Not draft-ready.**"
        )

    # Name: from decorator string arg or function name
    original_name = decorator_name if decorator_name else node.name
    func_name = to_snake(original_name) if decorator_name else node.name
    description = ast.get_docstring(node) or ""

    params, param_warnings = extract_params(node)
    result.warnings.extend(param_warnings)

    body = extract_function_body(node, source_lines)
    return_annotation = get_return_annotation(node)

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


def _is_basetool_subclass(node: ast.ClassDef) -> bool:
    """Check if class subclasses BaseTool (CrewAI style)."""
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
    """Extract CrewAI BaseTool using Pydantic field syntax."""
    class_name = node.name
    tool_name = ""
    tool_description = ""

    # CrewAI BaseTool uses Pydantic field syntax: name: str = "my_tool"
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if item.target.id == "name" and item.value and isinstance(item.value, ast.Constant):
                tool_name = str(item.value.value)
            elif item.target.id == "description" and item.value and isinstance(item.value, ast.Constant):
                tool_description = str(item.value.value)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    if target.id == "name" and isinstance(item.value, ast.Constant):
                        tool_name = str(item.value.value)
                    elif target.id == "description" and isinstance(item.value, ast.Constant):
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
        result.warnings.append(f"BaseTool `{class_name}` has no `_run()` method.")
        return None

    is_async = isinstance(run_method, ast.AsyncFunctionDef)
    if is_async:
        result.warnings.append(
            f"Function `{func_name}` uses async/await. "
            "AgentNode can currently only run synchronous tools. **Not draft-ready.**"
        )

    params, param_warnings = extract_params(run_method)
    result.warnings.extend(param_warnings)
    result.changes.append(f"`self` parameter removed from `{class_name}._run()`")

    body = extract_function_body(run_method, source_lines)
    self_refs = detect_self_references(body)
    has_self_refs = bool(self_refs)
    if has_self_refs:
        for ref in self_refs[:3]:
            result.warnings.append(
                f"Code references `{ref}`. In ANP there is no `self` — "
                "use a function parameter instead. **Not draft-ready.**"
            )

    return_annotation = get_return_annotation(run_method)

    body, has_return_dict, return_kind = apply_return_policy(
        func_name, return_annotation, body, result,
    )

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
