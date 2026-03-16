"""JSON processing tool with JMESPath queries and schema validation."""

from __future__ import annotations

import json
from typing import Any

import jmespath


def run(
    data: dict | list | str,
    query: str = "",
    validate_schema: dict | None = None,
) -> dict:
    """Process JSON data: query with JMESPath, validate against JSON Schema, or parse/format.

    Args:
        data: JSON data as a dict, list, or JSON string.
        query: A JMESPath query expression to apply.
        validate_schema: A JSON Schema dict to validate data against.

    Returns:
        A dict with the processing result.
    """
    # Parse string input
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            return {"error": f"Invalid JSON string: {exc}", "valid_json": False}
    else:
        parsed = data

    result: dict[str, Any] = {"valid_json": True}

    # Apply JMESPath query if provided
    if query:
        try:
            query_result = jmespath.search(query, parsed)
            result["query"] = query
            result["query_result"] = query_result
        except jmespath.exceptions.JMESPathError as exc:
            result["query"] = query
            result["query_error"] = str(exc)

    # Validate against schema if provided
    if validate_schema is not None:
        try:
            import jsonschema

            try:
                jsonschema.validate(instance=parsed, schema=validate_schema)
                result["schema_valid"] = True
                result["schema_errors"] = []
            except jsonschema.ValidationError as exc:
                result["schema_valid"] = False
                result["schema_errors"] = [
                    {
                        "message": exc.message,
                        "path": list(exc.absolute_path),
                        "validator": exc.validator,
                    }
                ]
            except jsonschema.SchemaError as exc:
                result["schema_valid"] = False
                result["schema_errors"] = [{"message": f"Invalid schema: {exc.message}"}]
        except ImportError:
            result["schema_valid"] = None
            result["schema_errors"] = [
                {"message": "jsonschema package not installed. Install with: pip install jsonschema"}
            ]

    # If no query and no schema validation, return formatted data info
    if not query and validate_schema is None:
        result["data"] = parsed
        result["formatted"] = json.dumps(parsed, indent=2, default=str)
        if isinstance(parsed, dict):
            result["type"] = "object"
            result["keys"] = list(parsed.keys())
            result["key_count"] = len(parsed)
        elif isinstance(parsed, list):
            result["type"] = "array"
            result["length"] = len(parsed)
            if parsed:
                result["first_element_type"] = type(parsed[0]).__name__
        else:
            result["type"] = type(parsed).__name__

    return result
