"""Generate test stubs for Python functions using ast parsing."""

from __future__ import annotations

import ast
import textwrap


def run(code: str, framework: str = "pytest", function_name: str = "") -> dict:
    """Generate test stubs for Python functions.

    Args:
        code: Python source code to generate tests for.
        framework: Test framework - "pytest" or "unittest".
        function_name: If set, generate tests only for this function.

    Returns:
        dict with keys: test_code, test_count, functions_covered.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"test_code": f"# Could not parse code: {exc}", "test_count": 0, "functions_covered": []}

    functions = _extract_functions(tree, function_name)

    if not functions:
        return {
            "test_code": "# No functions found to generate tests for.",
            "test_count": 0,
            "functions_covered": [],
        }

    if framework == "unittest":
        test_code = _generate_unittest(functions)
    else:
        test_code = _generate_pytest(functions)

    return {
        "test_code": test_code,
        "test_count": sum(len(f["test_cases"]) for f in functions),
        "functions_covered": [f["name"] for f in functions],
    }


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def _extract_functions(tree: ast.Module, only: str = "") -> list[dict]:
    """Walk the AST and extract function metadata."""
    results: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            # skip private helpers unless explicitly requested
            if only and node.name != only:
                continue
            if not only:
                continue
        if only and node.name != only:
            continue

        info = _function_info(node)
        results.append(info)

    return results


def _function_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    """Extract metadata from a function node."""
    params: list[dict] = []
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        annotation = ""
        if arg.annotation:
            annotation = ast.dump(arg.annotation)
            # Try to get a readable type name
            if isinstance(arg.annotation, ast.Name):
                annotation = arg.annotation.id
            elif isinstance(arg.annotation, ast.Constant):
                annotation = str(arg.annotation.value)
        params.append({"name": arg.arg, "type": annotation})

    # Return annotation
    return_type = ""
    if node.returns:
        if isinstance(node.returns, ast.Name):
            return_type = node.returns.id
        elif isinstance(node.returns, ast.Constant):
            return_type = str(node.returns.value)

    # Docstring
    docstring = ast.get_docstring(node) or ""

    # Determine test cases
    test_cases = _plan_test_cases(node, params, return_type, docstring)

    is_async = isinstance(node, ast.AsyncFunctionDef)

    return {
        "name": node.name,
        "params": params,
        "return_type": return_type,
        "docstring": docstring,
        "is_async": is_async,
        "test_cases": test_cases,
    }


def _plan_test_cases(
    node: ast.FunctionDef,
    params: list[dict],
    return_type: str,
    docstring: str,
) -> list[dict]:
    """Plan test cases based on function signature and body analysis."""
    cases: list[dict] = []

    # 1. Basic call test
    cases.append({
        "name": f"test_{node.name}_basic",
        "description": "Test basic invocation with typical arguments.",
        "args": _sample_args(params),
        "assertion": "is not None" if return_type else "passes without error",
    })

    # 2. Check if function has branches -> edge case test
    has_branches = any(isinstance(n, ast.If) for n in ast.walk(node))
    if has_branches:
        cases.append({
            "name": f"test_{node.name}_edge_case",
            "description": "Test edge case / alternate branch.",
            "args": _edge_args(params),
            "assertion": "handles edge case",
        })

    # 3. If there are params, test with empty/zero/None values
    if params:
        cases.append({
            "name": f"test_{node.name}_empty_input",
            "description": "Test with empty or minimal input.",
            "args": _empty_args(params),
            "assertion": "handles empty input",
        })

    # 4. Check for raise statements -> test exception
    has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(node))
    if has_raise:
        cases.append({
            "name": f"test_{node.name}_raises",
            "description": "Test that the function raises an appropriate exception.",
            "args": _edge_args(params),
            "assertion": "raises exception",
        })

    return cases


def _sample_args(params: list[dict]) -> dict:
    """Generate sample argument values based on type hints."""
    samples: dict = {}
    for p in params:
        samples[p["name"]] = _sample_value(p["type"])
    return samples


def _edge_args(params: list[dict]) -> dict:
    samples: dict = {}
    for p in params:
        samples[p["name"]] = _edge_value(p["type"])
    return samples


def _empty_args(params: list[dict]) -> dict:
    samples: dict = {}
    for p in params:
        samples[p["name"]] = _empty_value(p["type"])
    return samples


def _sample_value(type_hint: str) -> str:
    """Return a representative sample value as a code literal."""
    mapping = {
        "str": '"hello"',
        "int": "42",
        "float": "3.14",
        "bool": "True",
        "list": "[1, 2, 3]",
        "dict": '{"key": "value"}',
        "bytes": 'b"data"',
    }
    return mapping.get(type_hint, '"test_value"')


def _edge_value(type_hint: str) -> str:
    mapping = {
        "str": '""',
        "int": "-1",
        "float": "0.0",
        "bool": "False",
        "list": "[]",
        "dict": "{}",
        "bytes": 'b""',
    }
    return mapping.get(type_hint, "None")


def _empty_value(type_hint: str) -> str:
    mapping = {
        "str": '""',
        "int": "0",
        "float": "0.0",
        "bool": "False",
        "list": "[]",
        "dict": "{}",
        "bytes": 'b""',
    }
    return mapping.get(type_hint, "None")


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def _generate_pytest(functions: list[dict]) -> str:
    """Generate pytest-style test code."""
    lines: list[str] = [
        '"""Auto-generated test stubs."""',
        "",
        "import pytest",
        "",
        "# TODO: import the module under test",
        "# from my_module import ...",
        "",
        "",
    ]

    for func in functions:
        async_prefix = "async " if func["is_async"] else ""
        for case in func["test_cases"]:
            lines.append(f"{async_prefix}def {case['name']}():")
            lines.append(f'    """{case["description"]}"""')

            # Build the function call
            arg_str = ", ".join(f"{k}={v}" for k, v in case["args"].items())
            call = f"{func['name']}({arg_str})"

            if "raises" in case["assertion"]:
                lines.append(f"    with pytest.raises(Exception):")
                lines.append(f"        {call}")
            elif "is not None" in case["assertion"]:
                lines.append(f"    result = {call}")
                lines.append(f"    assert result is not None")
            else:
                lines.append(f"    result = {call}")
                lines.append(f"    # TODO: add specific assertions")
                lines.append(f"    assert result is not None or result is None  # placeholder")

            lines.append("")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _generate_unittest(functions: list[dict]) -> str:
    """Generate unittest-style test code."""
    lines: list[str] = [
        '"""Auto-generated test stubs."""',
        "",
        "import unittest",
        "",
        "# TODO: import the module under test",
        "# from my_module import ...",
        "",
        "",
        "class TestGenerated(unittest.TestCase):",
        "",
    ]

    for func in functions:
        for case in func["test_cases"]:
            lines.append(f"    def {case['name']}(self):")
            lines.append(f'        """{case["description"]}"""')

            arg_str = ", ".join(f"{k}={v}" for k, v in case["args"].items())
            call = f"{func['name']}({arg_str})"

            if "raises" in case["assertion"]:
                lines.append(f"        with self.assertRaises(Exception):")
                lines.append(f"            {call}")
            elif "is not None" in case["assertion"]:
                lines.append(f"        result = {call}")
                lines.append(f"        self.assertIsNotNone(result)")
            else:
                lines.append(f"        result = {call}")
                lines.append(f"        # TODO: add specific assertions")
                lines.append(f"        self.assertTrue(True)  # placeholder")

            lines.append("")

    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    unittest.main()")
    lines.append("")

    return "\n".join(lines)
