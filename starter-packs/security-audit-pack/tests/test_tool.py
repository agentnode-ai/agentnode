"""Tests for security-audit-pack."""


def test_run_detects_os_system():
    from security_audit_pack.tool import run

    result = run(code="import os\nos.system('ls')")
    assert "issues" in result
    assert "total" in result
    assert result["total"] >= 1


def test_run_safe_code():
    from security_audit_pack.tool import run

    result = run(code="x = 1 + 2\nprint(x)")
    assert isinstance(result["issues"], list)
