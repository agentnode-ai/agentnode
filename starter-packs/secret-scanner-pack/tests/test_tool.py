"""Tests for secret-scanner-pack."""


def test_run_detects_api_key():
    from secret_scanner_pack.tool import run

    result = run(code="API_KEY = 'sk_test_1234567890abcdef1234567890abcdef'")
    assert "findings" in result
    assert "total" in result
    assert result["total"] >= 1


def test_run_clean_code():
    from secret_scanner_pack.tool import run

    result = run(code="x = 42\nname = 'Alice'")
    assert result["total"] == 0
    assert result["findings"] == []
