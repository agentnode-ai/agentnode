"""Tests for test-generator-pack."""


def test_run_generates_tests():
    from test_generator_pack.tool import run

    code = "def add(a: int, b: int) -> int:\n    return a + b"
    result = run(code=code, framework="pytest")
    assert "test_code" in result
    assert result["test_count"] >= 1
    assert "add" in result["functions_covered"]
    assert "def test_" in result["test_code"]


def test_run_multiple_functions():
    from test_generator_pack.tool import run

    code = "def foo(): pass\ndef bar(x): return x"
    result = run(code=code)
    assert result["test_count"] >= 2
