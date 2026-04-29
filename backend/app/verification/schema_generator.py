"""Generate minimal test inputs from JSON Schema definitions.

Phase 1: generate_test_input() + NAME_HINTS + generate_candidates().
Phase 2A: is_incomplete_schema(), operation-field detection,
  schema examples priority, OPERATION_CANDIDATES fallback.
Phase 2B: Active enum probe — extract_enum_values(), _sort_by_safety(),
  build_probe_candidate().
"""

from __future__ import annotations

import re

MAX_RECURSION_DEPTH = 3

# ── Phase 2B: Enum value extraction from error messages ──

_BRACKET_LIST_RE = re.compile(r"\[([^\[\]]+)\]")       # HIGH confidence
_QUOTED_VALUE_RE = re.compile(r"""['"]([^'"]+)['"]""")  # HIGH (inside brackets)
_COMMA_LIST_RE = re.compile(                             # MEDIUM confidence
    r"(?:supported|available|valid|allowed|choose from|one of|options|values)"
    r"[\w\s]*[:\s]+([a-z_][a-z0-9_, -]+)", re.IGNORECASE,
)

# Safety prefixes: read-only operations first, mutating last
_SAFE_OPERATION_PREFIXES = (
    "list", "get", "read", "search", "status",
    "info", "show", "describe", "fetch", "count",
)

# Relaxed value pattern for comma-separated lists (allows hyphens)
_VALID_VALUE_RE = re.compile(r"^[a-z][a-z0-9_-]*$", re.IGNORECASE)


def extract_enum_values(error_msg: str) -> tuple[list[str], str]:
    """Extract enum values from an error message.

    Returns (values, confidence) where confidence is "high", "medium", or "none".
    """
    if not error_msg:
        return [], "none"

    # HIGH confidence: bracket list with quoted strings ['create_event', 'list_events']
    bracket_match = _BRACKET_LIST_RE.search(error_msg)
    if bracket_match:
        inner = bracket_match.group(1)
        quoted = _QUOTED_VALUE_RE.findall(inner)
        if quoted and len(quoted) >= 2:
            return quoted, "high"

    # MEDIUM confidence: keyword followed by comma-separated values
    comma_match = _COMMA_LIST_RE.search(error_msg)
    if comma_match:
        raw = comma_match.group(1).strip()
        values = [v.strip() for v in raw.split(",") if v.strip()]
        # Validate each value looks like a real enum value
        valid = [v for v in values if _VALID_VALUE_RE.match(v)]
        if len(valid) >= 2:
            return valid, "medium"

    return [], "none"


def _sort_by_safety(values: list[str]) -> list[str]:
    """Sort enum values with safe (read-only) operations first."""
    def safety_key(v: str) -> tuple[int, str]:
        v_lower = v.lower()
        for i, prefix in enumerate(_SAFE_OPERATION_PREFIXES):
            if v_lower.startswith(prefix):
                return (0, f"{i:02d}")
        return (1, v_lower)
    return sorted(values, key=safety_key)


def build_probe_candidate(
    base_input: dict,
    field_name: str,
    error_msg: str,
) -> dict | None:
    """Build a probe candidate by extracting enum values from an error message.

    Returns a new candidate dict with the field replaced by the safest extracted value,
    or None if extraction fails or confidence is too low.
    """
    values, confidence = extract_enum_values(error_msg)
    if confidence == "none" or not values:
        return None

    sorted_values = _sort_by_safety(values)
    probe_value = sorted_values[0]
    return {**base_input, field_name: probe_value}


def _find_operation_field_in_input(test_input: dict, schema: dict | None) -> str | None:
    """Find which field in a test input is an operation-like field.

    Checks against _OPERATION_FIELDS. Used to identify which field to replace
    in a probe candidate.
    """
    if not test_input or not schema:
        return None
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    for field_name in test_input:
        if field_name.lower() in _OPERATION_FIELDS:
            return field_name
    return None

# Smart defaults based on common parameter names
NAME_HINTS = {
    "text": "Hello world",
    "content": "Sample content for testing",
    "query": "test query",
    "search": "test",
    "prompt": "Say hello",
    "message": "Test message",
    "input": "test input",
    "data": "test data",
    "body": "Test body content",
    "title": "Test Title",
    "name": "test-item",
    "description": "A test description",
    "url": "https://example.com",
    "path": "/tmp/agentnode_verify/test.txt",
    "file_path": "/tmp/agentnode_verify/test.txt",
    "filename": "test.txt",
    "pdf_file": "/tmp/agentnode_verify/test.pdf",
    "image_path": "/tmp/agentnode_verify/test.png",
    "image_file": "/tmp/agentnode_verify/test.png",
    "document": "/tmp/agentnode_verify/test.pdf",
    "document_path": "/tmp/agentnode_verify/test.pdf",
    "input_document": "/tmp/agentnode_verify/test.pdf",
    "audio_file": "/tmp/agentnode_verify/test.wav",
    "audio_path": "/tmp/agentnode_verify/test.wav",
    "video_file": "/tmp/agentnode_verify/test.mp4",
    "source_path": "/tmp/agentnode_verify/test.txt",
    "email": "test@example.com",
    "subject": "Test Subject",
    "language": "en",
    "lang": "en",
    "source": "en",
    "target": "de",
    "format": "json",
    "output_format": "json",
    "model": "default",
    "channel": "general",
    "recipient": "test-user",
    "to": "test@example.com",
    "question": "What is this?",
    "code": "print('hello')",
    "sql": "SELECT 1",
    "regex": "\\w+",
    "pattern": "\\w+",
    "slug": "test-slug",
    "id": "test-id-123",
    "key": "test-key",
    "token": "test-token",
    "topic": "technology",
    "category": "general",
    "tags": ["test"],
    "labels": ["test"],
    "keywords": ["test"],
    "items": [],
    "options": {},
    "config": {},
    "settings": {},
    "params": {},
    "args": {},
    "kwargs": {},
    "operation": "list",
    "action": "list",
    "mode": "default",
    "method": "GET",
    "command": "help",
    "type": "text",
    "platform": "twitter",
    "provider": "default",
    "service": "default",
    "width": 800,
    "height": 600,
    "size": 100,
    "count": 5,
    "limit": 10,
    "max_results": 5,
    "page": 1,
    "timeout": 30,
    "retries": 1,
    "verbose": False,
    "debug": False,
    "force": False,
    "dry_run": True,
    "overwrite": False,
    "file": "/tmp/agentnode_verify/test.txt",
    "input_file": "/tmp/agentnode_verify/test.txt",
    "output_file": "/tmp/agentnode_verify/output.txt",
    "source_file": "/tmp/agentnode_verify/test.txt",
    "directory": "/tmp/agentnode_verify",
    "folder": "/tmp/agentnode_verify",
    "source_language": "en",
    "target_language": "de",
}


def generate_test_input(schema: dict | None, _depth: int = 0) -> dict:
    """Generate a minimal valid input from a JSON Schema.

    Rules:
    - Uses `default` if present
    - Uses first `enum` value if present
    - Uses NAME_HINTS for smart defaults based on parameter name
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
            result[key] = _generate_value(prop, _depth, key)

        return result
    except Exception:
        return {}


def _generate_value(prop: dict, _depth: int = 0, name: str = "") -> object:
    """Generate a single value from a property schema."""
    try:
        if "default" in prop:
            val = prop["default"]
            # Skip None defaults for required fields
            if val is not None:
                return val

        if "enum" in prop and prop["enum"]:
            return prop["enum"][0]

        # Check name hints for smart defaults
        name_lower = name.lower()
        if name_lower in NAME_HINTS:
            hint = NAME_HINTS[name_lower]
            prop_type = prop.get("type", "string")
            # Make sure the hint type matches the schema type
            if prop_type == "string" and isinstance(hint, str):
                return hint
            elif prop_type == "integer" and isinstance(hint, int):
                return hint
            elif prop_type == "number" and isinstance(hint, (int, float)):
                return hint
            elif prop_type == "boolean" and isinstance(hint, bool):
                return hint
            elif prop_type == "array" and isinstance(hint, list):
                return hint
            elif prop_type == "object" and isinstance(hint, dict):
                return hint

        prop_type = prop.get("type", "string")

        if prop_type == "string":
            # File-path inference from parameter name context
            if any(k in name_lower for k in ("file", "path", "document")):
                if "pdf" in name_lower:
                    return "/tmp/agentnode_verify/test.pdf"
                if "image" in name_lower or "img" in name_lower or "photo" in name_lower:
                    return "/tmp/agentnode_verify/test.png"
                if "audio" in name_lower or "sound" in name_lower or "wav" in name_lower:
                    return "/tmp/agentnode_verify/test.wav"
                if "video" in name_lower:
                    return "/tmp/agentnode_verify/test.mp4"
                if "docx" in name_lower or "word" in name_lower:
                    return "/tmp/agentnode_verify/test.docx"
                if "pptx" in name_lower or "powerpoint" in name_lower or "presentation" in name_lower:
                    return "/tmp/agentnode_verify/test.pptx"
                if "xlsx" in name_lower or "excel" in name_lower or "spreadsheet" in name_lower:
                    return "/tmp/agentnode_verify/test.xlsx"
            # Try partial name matching for strings (exact key match first)
            if name_lower in NAME_HINTS and isinstance(NAME_HINTS[name_lower], str):
                return NAME_HINTS[name_lower]
            # Partial match — only if the hint key is a SUFFIX of the name
            # (avoids "source" matching "source_file")
            for hint_key, hint_val in NAME_HINTS.items():
                if isinstance(hint_val, str) and name_lower.endswith(hint_key) and hint_key != name_lower:
                    return hint_val
            return "test"
        elif prop_type == "integer":
            return 1
        elif prop_type == "number":
            return 1.0
        elif prop_type == "boolean":
            return True
        elif prop_type == "array":
            # Generate one example item if items schema available
            items_schema = prop.get("items")
            if items_schema and isinstance(items_schema, dict) and _depth < MAX_RECURSION_DEPTH:
                item = _generate_value(items_schema, _depth + 1, "")
                return [item] if item else []
            # Provide a reasonable default for common array-of-dict patterns
            if any(k in name_lower for k in ("slides", "content", "blocks", "items", "rows", "entries")):
                return [{"text": "Test content", "title": "Test"}]
            return []
        elif prop_type == "object":
            nested = generate_test_input(prop, _depth + 1)
            # Provide defaults for common object patterns with no sub-schema
            if not nested and any(k in name_lower for k in ("source", "config", "data", "metadata")):
                return {"type": "book", "title": "Test", "authors": ["Test Author"], "year": "2024"}
            return nested
        else:
            return "test"
    except Exception:
        return "test"


def is_incomplete_schema(schema: dict | None) -> bool:
    """Check if a schema is declared but useless for input generation.

    Returns True for schemas that generate_test_input() cannot produce
    meaningful inputs from (e.g. {type: "object"} without properties).
    """
    if not schema or not isinstance(schema, dict):
        return True
    schema_type = schema.get("type")
    if schema_type == "object" and not schema.get("properties"):
        return True
    if schema_type == "array" and not schema.get("items"):
        return True
    return False


# Operation-like fields get multiple candidates to increase hit rate
OPERATION_CANDIDATES = ["list", "get", "search", "read", "status", "help"]

_OPERATION_FIELDS = frozenset({"operation", "action", "command", "mode", "method"})


def _find_operation_field(schema: dict) -> str | None:
    """Find a field that looks like an operation selector.

    Checks required fields first, then all properties.
    Only matches fields without enum/default (those are already handled).
    Only applies to object schemas with properties.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return None
    required = set(schema.get("required", []))

    # First pass: required fields only
    for field_name in required:
        if field_name.lower() in _OPERATION_FIELDS:
            prop = props.get(field_name, {})
            if not prop.get("enum") and "default" not in prop:
                return field_name

    # Second pass: any property (operation may be optional but still important)
    for field_name in props:
        if field_name in required:
            continue
        if field_name.lower() in _OPERATION_FIELDS:
            prop = props.get(field_name, {})
            if not prop.get("enum") and "default" not in prop:
                return field_name

    return None


def generate_candidates(
    schema: dict | None,
    examples: list[dict] | None = None,
) -> list[dict]:
    """Generate an ordered list of candidate inputs for smoke testing.

    Priority (Phase 2A):
      1. Schema "examples" keyword (explicit example > heuristic)
      2. generate_test_input() (enum/default/NAME_HINTS)
      3. Alternative operation value (if operation-like field detected)

    Returns list of dicts, max 2. May be empty if schema is None/empty.
    """
    candidates: list[dict] = []

    # ── 1. Schema "examples" keyword (highest confidence) ──
    if schema and isinstance(schema, dict):
        schema_examples = schema.get("examples")
        if isinstance(schema_examples, list):
            for ex in schema_examples:
                if isinstance(ex, dict):
                    candidates.append(ex)
                    break

    # ── 2. Schema-derived (enum/default/NAME_HINTS) ──
    primary = generate_test_input(schema)
    if primary and primary not in candidates:
        candidates.append(primary)

    # ── 3. Alternative operation value ──
    base = candidates[0] if candidates else primary
    if schema and base:
        op_field = _find_operation_field(schema)
        if op_field and op_field in base:
            current_val = base[op_field]
            for alt in OPERATION_CANDIDATES:
                if alt != current_val:
                    alt_candidate = {**base, op_field: alt}
                    if alt_candidate not in candidates:
                        candidates.append(alt_candidate)
                    break

    # Last resort
    if not candidates:
        candidates.append({})

    return candidates[:2]
