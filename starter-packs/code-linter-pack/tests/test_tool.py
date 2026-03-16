"""Tests for code-linter-pack."""

import pytest


def test_run_clean_code():
    from code_linter_pack.tool import run

    result = run(code="x = 1\nprint(x)\n")
    assert "issues" in result or "errors" in result or "results" in result


def test_run_code_with_issues():
    from code_linter_pack.tool import run

    code = "import os\nimport sys\nx=1\n"
    result = run(code=code)
    assert isinstance(result, dict)
