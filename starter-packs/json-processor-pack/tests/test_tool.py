"""Tests for json-processor-pack."""

import pytest


def test_run_parse_dict():
    """Test processing a dict returns formatted output."""
    from json_processor_pack.tool import run

    result = run({"name": "Alice", "age": 30})

    assert result["valid_json"] is True
    assert result["type"] == "object"
    assert result["keys"] == ["name", "age"]
    assert result["key_count"] == 2
    assert "formatted" in result


def test_run_parse_list():
    """Test processing a list returns array info."""
    from json_processor_pack.tool import run

    result = run([1, 2, 3])

    assert result["valid_json"] is True
    assert result["type"] == "array"
    assert result["length"] == 3
    assert result["first_element_type"] == "int"


def test_run_parse_json_string():
    """Test parsing a JSON string."""
    from json_processor_pack.tool import run

    result = run('{"key": "value"}')

    assert result["valid_json"] is True
    assert result["type"] == "object"
    assert result["data"] == {"key": "value"}


def test_run_invalid_json_string():
    """Test that invalid JSON returns error."""
    from json_processor_pack.tool import run

    result = run("{not valid json}")

    assert result["valid_json"] is False
    assert "error" in result


def test_run_jmespath_query():
    """Test JMESPath query execution."""
    from json_processor_pack.tool import run

    data = {"users": [{"name": "Alice"}, {"name": "Bob"}]}
    result = run(data, query="users[*].name")

    assert result["valid_json"] is True
    assert result["query"] == "users[*].name"
    assert result["query_result"] == ["Alice", "Bob"]


def test_run_jmespath_invalid_query():
    """Test that invalid JMESPath query returns error."""
    from json_processor_pack.tool import run

    result = run({"a": 1}, query="[invalid!!")

    assert "query_error" in result


def test_run_schema_validation_pass():
    """Test JSON Schema validation when data is valid."""
    from json_processor_pack.tool import run

    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name"],
    }
    result = run({"name": "Alice", "age": 30}, validate_schema=schema)

    assert result["valid_json"] is True
    assert result["schema_valid"] is True
    assert result["schema_errors"] == []


def test_run_schema_validation_fail():
    """Test JSON Schema validation when data is invalid."""
    from json_processor_pack.tool import run

    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    result = run({"age": 30}, validate_schema=schema)

    assert result["schema_valid"] is False
    assert len(result["schema_errors"]) > 0


def test_run_query_and_schema_together():
    """Test using both query and schema validation."""
    from json_processor_pack.tool import run

    data = {"items": [1, 2, 3]}
    schema = {"type": "object", "required": ["items"]}

    result = run(data, query="items[0]", validate_schema=schema)

    assert result["query_result"] == 1
    assert result["schema_valid"] is True


def test_run_empty_list():
    """Test processing an empty list."""
    from json_processor_pack.tool import run

    result = run([])

    assert result["valid_json"] is True
    assert result["type"] == "array"
    assert result["length"] == 0
