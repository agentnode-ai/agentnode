"""Tests for regex-builder-pack."""

import pytest


def test_run_test_match():
    """Test basic regex matching."""
    from regex_builder_pack.tool import run

    result = run(r"\d+", test_string="abc 123 def", operation="test")

    assert result["match"] is True
    assert result["matched_text"] == "123"
    assert result["span"] == [4, 7]


def test_run_test_no_match():
    """Test regex that doesn't match."""
    from regex_builder_pack.tool import run

    result = run(r"\d+", test_string="no digits here", operation="test")

    assert result["match"] is False
    assert result["matched_text"] is None


def test_run_findall():
    """Test findall operation."""
    from regex_builder_pack.tool import run

    result = run(r"\b\w+@\w+\.\w+\b", test_string="a@b.c and d@e.f", operation="findall")

    assert result["count"] == 2
    assert len(result["matches"]) == 2
    assert result["matches"][0]["text"] == "a@b.c"
    assert result["matches"][1]["text"] == "d@e.f"


def test_run_split():
    """Test split operation."""
    from regex_builder_pack.tool import run

    result = run(r"[,;]\s*", test_string="a, b; c, d", operation="split")

    assert result["parts"] == ["a", "b", "c", "d"]
    assert result["count"] == 4


def test_run_sub():
    """Test substitution operation."""
    from regex_builder_pack.tool import run

    result = run(r"\d+", test_string="abc 123 def 456", operation="sub", replacement="NUM")

    assert result["result"] == "abc NUM def NUM"
    assert result["substitutions_made"] == 2


def test_run_named_groups():
    """Test named capture groups."""
    from regex_builder_pack.tool import run

    result = run(
        r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})",
        test_string="Date: 2026-03-16",
        operation="test",
    )

    assert result["match"] is True
    assert result["named_groups"] == {"year": "2026", "month": "03", "day": "16"}
    assert result["group_names"] == ["year", "month", "day"]


def test_run_flags_ignorecase():
    """Test IGNORECASE flag."""
    from regex_builder_pack.tool import run

    result = run(r"hello", test_string="HELLO world", operation="test", flags=["IGNORECASE"])

    assert result["match"] is True
    assert result["matched_text"] == "HELLO"


def test_run_invalid_pattern():
    """Test that invalid regex returns error."""
    from regex_builder_pack.tool import run

    result = run(r"[invalid", test_string="test")

    assert "error" in result
    assert "Invalid regex" in result["error"]


def test_run_no_test_string():
    """Test pattern compilation without test string."""
    from regex_builder_pack.tool import run

    result = run(r"(\w+)\s+(\w+)")

    assert result["groups_in_pattern"] == 2
    assert result["note"] == "No test_string provided. Pattern compiled successfully."


def test_run_full_match():
    """Test full_match detection."""
    from regex_builder_pack.tool import run

    result = run(r"\d+", test_string="123", operation="test")

    assert result["match"] is True
    assert result["full_match"] is True

    result2 = run(r"\d+", test_string="abc123", operation="test")

    assert result2["match"] is True
    assert result2["full_match"] is False


def test_run_unknown_operation():
    """Test unknown operation returns error."""
    from regex_builder_pack.tool import run

    result = run(r"test", test_string="test", operation="invalid")

    assert "error" in result
