"""Tests for api-docs-generator-pack."""


def test_run_markdown_output():
    from api_docs_generator_pack.tool import run

    code = '''
def greet(name: str) -> str:
    """Say hello to someone."""
    return f"Hello {name}"

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
'''
    result = run(code=code, format="markdown", title="Test API")
    assert "documentation" in result
    assert result["format"] == "markdown"
    assert result["endpoints"] >= 2
    assert "greet" in result["documentation"]


def test_run_empty_code():
    from api_docs_generator_pack.tool import run

    result = run(code="x = 1", format="markdown")
    assert "documentation" in result
    assert result["endpoints"] == 0
