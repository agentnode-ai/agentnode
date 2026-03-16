"""Tests for code-executor-pack."""

import pytest


def test_run_simple_print():
    """Test executing a simple print statement."""
    from code_executor_pack.tool import run

    result = run("print('hello world')")

    assert result["stdout"].strip() == "hello world"
    assert result["stderr"] == ""
    assert result["return_code"] == 0
    assert result["execution_time"] >= 0


def test_run_math_expression():
    """Test executing arithmetic code."""
    from code_executor_pack.tool import run

    result = run("print(2 + 3)")

    assert result["stdout"].strip() == "5"
    assert result["return_code"] == 0


def test_run_syntax_error():
    """Test that syntax errors are captured in stderr."""
    from code_executor_pack.tool import run

    result = run("def broken(:")

    assert result["return_code"] != 0
    assert "SyntaxError" in result["stderr"]


def test_run_runtime_error():
    """Test that runtime errors are captured."""
    from code_executor_pack.tool import run

    result = run("raise ValueError('test error')")

    assert result["return_code"] != 0
    assert "ValueError" in result["stderr"]


def test_run_multiline_code():
    """Test multi-line code execution."""
    from code_executor_pack.tool import run

    code = """
items = [1, 2, 3, 4, 5]
total = sum(items)
print(f"Sum: {total}")
"""
    result = run(code)

    assert result["stdout"].strip() == "Sum: 15"
    assert result["return_code"] == 0


def test_run_unsupported_language():
    """Test that unsupported language returns error."""
    from code_executor_pack.tool import run

    result = run("console.log('hi')", language="javascript")

    assert result["return_code"] == 1
    assert "Unsupported language" in result["stderr"]


def test_run_timeout():
    """Test that code exceeding timeout is killed."""
    from code_executor_pack.tool import run

    code = "import time; time.sleep(10)"
    result = run(code, timeout=2)

    assert result["return_code"] == -1
    assert "timed out" in result["stderr"]


def test_run_empty_output():
    """Test code that produces no output."""
    from code_executor_pack.tool import run

    result = run("x = 42")

    assert result["stdout"] == ""
    assert result["return_code"] == 0


def test_run_stderr_output():
    """Test that stderr is captured separately."""
    from code_executor_pack.tool import run

    code = "import sys; sys.stderr.write('warning\\n'); print('ok')"
    result = run(code)

    assert "ok" in result["stdout"]
    assert "warning" in result["stderr"]
    assert result["return_code"] == 0
