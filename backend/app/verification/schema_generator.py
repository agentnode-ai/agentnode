"""Generate minimal test inputs from JSON Schema definitions."""

from __future__ import annotations

MAX_RECURSION_DEPTH = 3


def generate_test_input(schema: dict | None, _depth: int = 0) -> dict:
    """Generate a minimal valid input from a JSON Schema.

    Rules:
    - Uses `default` if present
    - Uses first `enum` value if present
    - Falls back to type-based defaults
    - Recursively handles objects (required fields only, max depth 3)
    """
    try:
        if _depth >= MAX_RECURSION_DEPTH:
            return {}

        if not schema or not isinstance(schema, dict):
            return {}

        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return {}

        required = set(schema.get("required", []))

        if not properties:
            return {}

        result = {}
        for key, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            if key not in required and not prop.get("default"):
                continue
            result[key] = _generate_value(prop, _depth)

        return result
    except Exception:
        return {}


def _generate_value(prop: dict, _depth: int = 0) -> object:
    """Generate a single value from a property schema."""
    try:
        if "default" in prop:
            return prop["default"]

        if "enum" in prop and prop["enum"]:
            return prop["enum"][0]

        prop_type = prop.get("type", "string")

        if prop_type == "string":
            return "test"
        elif prop_type == "integer":
            return 1
        elif prop_type == "number":
            return 1.0
        elif prop_type == "boolean":
            return True
        elif prop_type == "array":
            return []
        elif prop_type == "object":
            return generate_test_input(prop, _depth + 1)
        else:
            return "test"
    except Exception:
        return "test"
