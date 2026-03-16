"""Analyze and refactor Python code using ast analysis and rope."""

from __future__ import annotations

import ast
import tempfile
import textwrap
from pathlib import Path


def run(code: str, operation: str = "analyze", **kwargs) -> dict:
    """Analyze or refactor Python code.

    Args:
        code: The Python source code to work with.
        operation: One of "analyze", "rename", or "extract_function".
        **kwargs: Extra arguments depending on the operation:
            - rename: old_name (str), new_name (str)
            - extract_function: start_line (int), end_line (int), function_name (str)

    Returns:
        dict with keys: result, changes.
    """
    operations = {
        "analyze": _analyze,
        "rename": _rename,
        "extract_function": _extract_function,
    }

    handler = operations.get(operation)
    if handler is None:
        return {
            "result": f"Unknown operation: {operation}. Supported: {', '.join(operations)}",
            "changes": [],
        }

    try:
        return handler(code, **kwargs)
    except Exception as exc:
        return {"result": f"Error during {operation}: {exc}", "changes": []}


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

def _analyze(code: str, **_kwargs) -> dict:
    """Analyze code for common code smells using the ast module."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"result": f"Syntax error: {exc}", "changes": []}

    smells: list[dict] = []
    lines = code.splitlines()

    for node in ast.walk(tree):
        # Long functions (> 30 statements)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_len = _count_statements(node)
            if body_len > 30:
                smells.append({
                    "type": "long_function",
                    "name": node.name,
                    "line": node.lineno,
                    "detail": f"Function has {body_len} statements (recommended max: 30).",
                })

            # Too many arguments (> 5)
            arg_count = len(node.args.args)
            if arg_count > 5:
                smells.append({
                    "type": "too_many_arguments",
                    "name": node.name,
                    "line": node.lineno,
                    "detail": f"Function has {arg_count} parameters (recommended max: 5).",
                })

            # Missing docstring
            if not _has_docstring(node):
                smells.append({
                    "type": "missing_docstring",
                    "name": node.name,
                    "line": node.lineno,
                    "detail": "Function lacks a docstring.",
                })

        # Deeply nested blocks (> 4 levels)
        if isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            depth = _nesting_depth(node)
            if depth > 4:
                smells.append({
                    "type": "deep_nesting",
                    "name": "",
                    "line": node.lineno,
                    "detail": f"Block nesting depth is {depth} (recommended max: 4).",
                })

        # Long lines (> 120 chars)
    for i, line in enumerate(lines, 1):
        if len(line) > 120:
            smells.append({
                "type": "long_line",
                "name": "",
                "line": i,
                "detail": f"Line is {len(line)} characters (recommended max: 120).",
            })

    summary = f"Found {len(smells)} potential code smell(s)." if smells else "No code smells detected."
    return {"result": summary, "changes": smells}


def _count_statements(node: ast.AST) -> int:
    """Count total statements recursively inside a node."""
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.stmt) and child is not node:
            count += 1
    return count


def _has_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if node.body and isinstance(node.body[0], ast.Expr):
        val = node.body[0].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            return True
    return False


def _nesting_depth(node: ast.AST, _depth: int = 0) -> int:
    """Return the maximum nesting depth starting from *node*."""
    max_depth = _depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            max_depth = max(max_depth, _nesting_depth(child, _depth + 1))
    return max_depth


# ---------------------------------------------------------------------------
# Rename (via rope)
# ---------------------------------------------------------------------------

def _rename(code: str, *, old_name: str = "", new_name: str = "", **_kw) -> dict:
    """Rename a symbol throughout the code using rope."""
    if not old_name or not new_name:
        return {"result": "Both old_name and new_name are required.", "changes": []}

    try:
        from rope.base.project import Project
        from rope.refactor.rename import Rename
    except ImportError:
        return {"result": "rope is not installed.", "changes": []}

    tmpdir = tempfile.mkdtemp()
    try:
        project = Project(tmpdir)
        file_path = Path(tmpdir) / "target.py"
        file_path.write_text(code, encoding="utf-8")

        resource = project.get_resource("target.py")
        source = resource.read()

        # Find the offset of the old name
        offset = source.find(old_name)
        if offset == -1:
            return {"result": f"Symbol '{old_name}' not found in code.", "changes": []}

        renamer = Rename(project, resource, offset)
        change_set = renamer.get_changes(new_name)
        project.do(change_set)

        new_code = resource.read()
        project.close()

        return {
            "result": f"Renamed '{old_name}' to '{new_name}' successfully.",
            "changes": [{"type": "rename", "old": old_name, "new": new_name, "refactored_code": new_code}],
        }
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Extract function (via rope)
# ---------------------------------------------------------------------------

def _extract_function(
    code: str,
    *,
    start_line: int = 0,
    end_line: int = 0,
    function_name: str = "extracted",
    **_kw,
) -> dict:
    """Extract a range of lines into a new function using rope."""
    if start_line <= 0 or end_line <= 0 or end_line < start_line:
        return {"result": "Valid start_line and end_line are required.", "changes": []}

    try:
        from rope.base.project import Project
        from rope.refactor.extract import ExtractMethod
    except ImportError:
        return {"result": "rope is not installed.", "changes": []}

    tmpdir = tempfile.mkdtemp()
    try:
        project = Project(tmpdir)
        file_path = Path(tmpdir) / "target.py"
        file_path.write_text(code, encoding="utf-8")

        resource = project.get_resource("target.py")
        source = resource.read()

        # Convert line numbers to character offsets
        lines = source.splitlines(True)
        if start_line > len(lines) or end_line > len(lines):
            return {"result": "Line numbers out of range.", "changes": []}

        start_offset = sum(len(lines[i]) for i in range(start_line - 1))
        end_offset = sum(len(lines[i]) for i in range(end_line))

        extractor = ExtractMethod(project, resource, start_offset, end_offset)
        change_set = extractor.get_changes(function_name)
        project.do(change_set)

        new_code = resource.read()
        project.close()

        return {
            "result": f"Extracted lines {start_line}-{end_line} into function '{function_name}'.",
            "changes": [{"type": "extract_function", "name": function_name, "refactored_code": new_code}],
        }
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
