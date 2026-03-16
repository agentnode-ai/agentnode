"""Tests for csv-analyzer-pack."""

import os
import tempfile

import pytest


@pytest.fixture
def sample_csv():
    """Create a temporary CSV file for testing."""
    content = "name,age,score\nAlice,30,95.5\nBob,25,87.0\nCharlie,35,91.2\nDiana,28,78.3\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        yield f.name
    os.unlink(f.name)


def test_run_describe(sample_csv):
    """Test describe operation returns statistics."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="describe")

    assert result["operation"] == "describe"
    assert result["rows"] == 4
    assert result["columns"] == 3
    assert "statistics" in result


def test_run_head(sample_csv):
    """Test head operation returns first N rows."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="head", n=2)

    assert result["operation"] == "head"
    assert result["n"] == 2
    assert result["total_rows"] == 4
    assert len(result["rows"]) == 2
    assert result["rows"][0]["name"] == "Alice"
    assert result["rows"][1]["name"] == "Bob"


def test_run_columns(sample_csv):
    """Test columns operation returns column info."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="columns")

    assert result["operation"] == "columns"
    assert result["total_columns"] == 3

    col_names = [c["name"] for c in result["column_info"]]
    assert "name" in col_names
    assert "age" in col_names
    assert "score" in col_names

    # Check numeric column has min/max/mean
    age_col = next(c for c in result["column_info"] if c["name"] == "age")
    assert age_col["min"] == 25.0
    assert age_col["max"] == 35.0


def test_run_filter_equals(sample_csv):
    """Test filter operation with == operator."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="filter", column="name", value="Alice")

    assert result["operation"] == "filter"
    assert result["matched_rows"] == 1
    assert result["rows"][0]["name"] == "Alice"


def test_run_filter_greater_than(sample_csv):
    """Test filter with > operator."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="filter", column="age", value=28, operator=">")

    assert result["matched_rows"] == 2  # Alice (30) and Charlie (35)


def test_run_filter_invalid_column(sample_csv):
    """Test filter with non-existent column returns error."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="filter", column="nonexistent", value="x")

    assert "error" in result


def test_run_filter_invalid_operator(sample_csv):
    """Test filter with invalid operator returns error."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="filter", column="age", value=30, operator="~=")

    assert "error" in result


def test_run_unknown_operation(sample_csv):
    """Test unknown operation returns error."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="pivot")

    assert "error" in result


def test_run_head_default_n(sample_csv):
    """Test head with default n=5."""
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="head")

    assert result["n"] == 5
    assert len(result["rows"]) == 4  # Only 4 rows in data
