"""Tests for code-refactor-pack."""


def test_run_analyze():
    from code_refactor_pack.tool import run

    code = "def foo(x):\n    return x * 2\n\ndef bar(y):\n    return y + 1\n"
    result = run(code=code, operation="analyze")
    assert isinstance(result, dict)
    assert "changes" in result or "result" in result
