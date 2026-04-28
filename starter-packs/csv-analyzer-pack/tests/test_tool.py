"""Tests for csv-analyzer-pack."""

import os
import tempfile

import pytest
from agentnode_sdk.exceptions import AgentNodeToolError


@pytest.fixture
def sample_csv():
    content = "name,age,score\nAlice,30,95.5\nBob,25,87.0\nCharlie,35,91.2\nDiana,28,78.3\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        yield f.name
    os.unlink(f.name)


def test_run_describe(sample_csv):
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="describe")
    assert result["operation"] == "describe"
    assert result["rows"] == 4
    assert result["columns"] == 3
    assert "statistics" in result


def test_run_head(sample_csv):
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="head", n=2)
    assert result["operation"] == "head"
    assert result["n"] == 2
    assert result["total_rows"] == 4
    assert len(result["rows"]) == 2
    assert result["rows"][0]["name"] == "Alice"
    assert result["rows"][1]["name"] == "Bob"


def test_run_columns(sample_csv):
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="columns")
    assert result["operation"] == "columns"
    assert result["total_columns"] == 3

    col_names = [c["name"] for c in result["column_info"]]
    assert "name" in col_names
    assert "age" in col_names

    age_col = next(c for c in result["column_info"] if c["name"] == "age")
    assert age_col["min"] == 25.0
    assert age_col["max"] == 35.0


def test_run_filter_equals(sample_csv):
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="filter", column="name", value="Alice")
    assert result["operation"] == "filter"
    assert result["matched_rows"] == 1
    assert result["rows"][0]["name"] == "Alice"


def test_run_filter_greater_than(sample_csv):
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="filter", column="age", value=28, operator=">")
    assert result["matched_rows"] == 2


def test_run_filter_invalid_column(sample_csv):
    from csv_analyzer_pack.tool import run

    with pytest.raises(AgentNodeToolError, match="not found"):
        run(sample_csv, operation="filter", column="nonexistent", value="x")


def test_run_filter_invalid_operator(sample_csv):
    from csv_analyzer_pack.tool import run

    with pytest.raises(AgentNodeToolError, match="Unknown operator"):
        run(sample_csv, operation="filter", column="age", value=30, operator="~=")


def test_run_unknown_operation(sample_csv):
    from csv_analyzer_pack.tool import run

    with pytest.raises(AgentNodeToolError, match="Unknown operation"):
        run(sample_csv, operation="pivot")


def test_run_head_default_n(sample_csv):
    from csv_analyzer_pack.tool import run

    result = run(sample_csv, operation="head")
    assert result["n"] == 5
    assert len(result["rows"]) == 4
