"""Tests for Phase 6.3: Schema-aware MCP Argument Inspection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnode_sdk.guard import (
    inspect_mcp_args,
    reset_guard_config_cache,
    reset_rate_limits,
    _safe_schema,
    _validate_field,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _guard_env(tmp_path, monkeypatch):
    """Permissive guard config so we isolate MCP inspection behavior."""
    cfg = {
        "version": "1",
        "trust": {"minimum_trust_level": "verified"},
        "permissions": {"network": "allow", "filesystem": "allow", "code_execution": "sandboxed"},
        "risk_policies": {"external_write_capable": "allow"},
        "guard": {
            "delete": "allow",
            "write_external": "allow",
            "execute": "allow",
            "credential_use": "allow",
            "network_egress": "allow",
            "write_local": "allow",
            "read": "allow",
            "compute": "allow",
            "unknown": "allow",
        },
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    reset_guard_config_cache()
    reset_rate_limits()
    yield
    reset_guard_config_cache()
    reset_rate_limits()


def _entry(**overrides):
    base = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "trust_level": "verified",
        "permissions": {},
    }
    base.update(overrides)
    return base


def _schema(properties: dict, required: list[str] | None = None):
    s = {"type": "object", "properties": properties}
    if required is not None:
        s["required"] = required
    return s


# ---------------------------------------------------------------------------
# 1. Type mismatch → prompt
# ---------------------------------------------------------------------------

class TestTypeMismatch:
    def test_string_expects_int(self):
        schema = _schema({"count": {"type": "integer"}})
        result = inspect_mcp_args("p", "t", {"count": "five"}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Type mismatch" in result.reason

    def test_int_expects_string(self):
        schema = _schema({"name": {"type": "string"}})
        result = inspect_mcp_args("p", "t", {"name": 42}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Type mismatch" in result.reason

    def test_bool_expects_object(self):
        schema = _schema({"config": {"type": "object"}})
        result = inspect_mcp_args("p", "t", {"config": True}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Type mismatch" in result.reason

    def test_int_valid_for_number(self):
        schema = _schema({"amount": {"type": "number"}})
        result = inspect_mcp_args("p", "t", {"amount": 42}, _entry(), input_schema=schema)
        assert result.action == "allow"

    def test_correct_type_passes(self):
        schema = _schema({"name": {"type": "string"}})
        result = inspect_mcp_args("p", "t", {"name": "hello"}, _entry(), input_schema=schema)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 2. Missing required field → prompt
# ---------------------------------------------------------------------------

class TestMissingRequired:
    def test_missing_required(self):
        schema = _schema({"query": {"type": "string"}}, required=["query"])
        result = inspect_mcp_args("p", "t", {}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Missing required" in result.reason

    def test_required_present(self):
        schema = _schema({"query": {"type": "string"}}, required=["query"])
        result = inspect_mcp_args("p", "t", {"query": "hello"}, _entry(), input_schema=schema)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 3. Enum violation → prompt
# ---------------------------------------------------------------------------

class TestEnumViolation:
    def test_value_not_in_enum(self):
        schema = _schema({"mode": {"type": "string", "enum": ["fast", "slow"]}})
        result = inspect_mcp_args("p", "t", {"mode": "turbo"}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Enum violation" in result.reason

    def test_value_in_enum(self):
        schema = _schema({"mode": {"type": "string", "enum": ["fast", "slow"]}})
        result = inspect_mcp_args("p", "t", {"mode": "fast"}, _entry(), input_schema=schema)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 4. Number min/max → prompt
# ---------------------------------------------------------------------------

class TestNumberConstraints:
    def test_below_minimum(self):
        schema = _schema({"count": {"type": "integer", "minimum": 1}})
        result = inspect_mcp_args("p", "t", {"count": 0}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "too small" in result.reason

    def test_above_maximum(self):
        schema = _schema({"count": {"type": "integer", "maximum": 100}})
        result = inspect_mcp_args("p", "t", {"count": 101}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "too large" in result.reason

    def test_within_range(self):
        schema = _schema({"count": {"type": "integer", "minimum": 1, "maximum": 100}})
        result = inspect_mcp_args("p", "t", {"count": 50}, _entry(), input_schema=schema)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 5. String length min/max → prompt
# ---------------------------------------------------------------------------

class TestStringLengthConstraints:
    def test_too_short(self):
        schema = _schema({"name": {"type": "string", "minLength": 3}})
        result = inspect_mcp_args("p", "t", {"name": "ab"}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "too short" in result.reason

    def test_too_long(self):
        schema = _schema({"name": {"type": "string", "maxLength": 5}})
        result = inspect_mcp_args("p", "t", {"name": "toolong"}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "too long" in result.reason

    def test_within_length(self):
        schema = _schema({"name": {"type": "string", "minLength": 1, "maxLength": 10}})
        result = inspect_mcp_args("p", "t", {"name": "hello"}, _entry(), input_schema=schema)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 6. Array items min/max → prompt
# ---------------------------------------------------------------------------

class TestArrayConstraints:
    def test_too_few_items(self):
        schema = _schema({"tags": {"type": "array", "minItems": 1}})
        result = inspect_mcp_args("p", "t", {"tags": []}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Too few items" in result.reason

    def test_too_many_items(self):
        schema = _schema({"tags": {"type": "array", "maxItems": 2}})
        result = inspect_mcp_args("p", "t", {"tags": ["a", "b", "c"]}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Too many items" in result.reason

    def test_within_bounds(self):
        schema = _schema({"tags": {"type": "array", "minItems": 1, "maxItems": 5}})
        result = inspect_mcp_args("p", "t", {"tags": ["a", "b"]}, _entry(), input_schema=schema)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 7. Malformed schema → heuristic fallback (current behavior unchanged)
# ---------------------------------------------------------------------------

class TestMalformedSchema:
    def test_schema_not_dict(self):
        result = inspect_mcp_args("p", "t", {"x": "safe"}, _entry(), input_schema="not-a-dict")
        assert result.action == "allow"

    def test_schema_root_type_not_object(self):
        result = inspect_mcp_args("p", "t", {"x": "safe"}, _entry(), input_schema={"type": "array"})
        assert result.action == "allow"

    def test_schema_properties_not_dict(self):
        result = inspect_mcp_args("p", "t", {"x": "safe"}, _entry(), input_schema={"type": "object", "properties": "bad"})
        assert result.action == "allow"

    def test_malformed_schema_still_catches_deny(self):
        result = inspect_mcp_args(
            "p", "t", {"path": "../../../etc/passwd"}, _entry(),
            input_schema="not-a-dict",
        )
        assert result.action == "deny"
        assert "path_traversal" in result.source or "Path traversal" in result.reason


# ---------------------------------------------------------------------------
# 8. No schema → current behavior unchanged
# ---------------------------------------------------------------------------

class TestNoSchema:
    def test_no_schema_safe_args(self):
        result = inspect_mcp_args("p", "t", {"query": "hello"}, _entry())
        assert result.action == "allow"

    def test_no_schema_dangerous_args(self):
        result = inspect_mcp_args("p", "t", {"path": "../../../etc/passwd"}, _entry())
        assert result.action == "deny"

    def test_no_schema_shell_tokens(self):
        result = inspect_mcp_args("p", "t", {"cmd": "rm -rf / && echo pwned"}, _entry())
        assert result.action == "prompt"
        assert "Shell token" in result.reason

    def test_none_schema_same_as_no_schema(self):
        result = inspect_mcp_args("p", "t", {"query": "hello"}, _entry(), input_schema=None)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 9. Shell tokens in free-text field → suppressed
# ---------------------------------------------------------------------------

class TestFreeTextSuppression:
    def test_freetext_no_maxlength_suppresses_shell(self):
        schema = _schema({"body": {"type": "string"}})
        result = inspect_mcp_args("p", "t", {"body": "echo $HOME && rm -rf /tmp"}, _entry(), input_schema=schema)
        assert result.action == "allow"

    def test_freetext_large_maxlength_suppresses_shell(self):
        schema = _schema({"body": {"type": "string", "maxLength": 5000}})
        result = inspect_mcp_args("p", "t", {"body": "echo $HOME && rm -rf /tmp"}, _entry(), input_schema=schema)
        assert result.action == "allow"

    def test_short_maxlength_does_not_suppress_shell(self):
        schema = _schema({"cmd": {"type": "string", "maxLength": 100}})
        result = inspect_mcp_args("p", "t", {"cmd": "ls; rm -rf /tmp"}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Shell token" in result.reason

    def test_non_string_field_does_not_suppress(self):
        schema = _schema({"cmd": {"type": "integer"}})
        result = inspect_mcp_args("p", "t", {"cmd": "ls; rm -rf /tmp"}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "Shell token" in result.reason or "Type mismatch" in result.reason


# ---------------------------------------------------------------------------
# 10. URL field with format uri/url → suppresses URL anomaly
# ---------------------------------------------------------------------------

class TestUrlFieldSuppression:
    def test_url_field_suppresses_anomaly(self):
        schema = _schema({"endpoint": {"type": "string", "format": "uri"}})
        entry = _entry(permissions={"network_level": "none"})
        result = inspect_mcp_args("p", "t", {"endpoint": "https://example.com"}, entry, input_schema=schema)
        assert result.action == "allow"

    def test_url_format_url_suppresses(self):
        schema = _schema({"link": {"type": "string", "format": "url"}})
        entry = _entry(permissions={"network_level": "none"})
        result = inspect_mcp_args("p", "t", {"link": "https://example.com"}, entry, input_schema=schema)
        assert result.action == "allow"

    def test_non_url_field_does_not_suppress(self):
        schema = _schema({"data": {"type": "string"}})
        entry = _entry(permissions={"network_level": "none"})
        result = inspect_mcp_args("p", "t", {"data": "https://example.com"}, entry, input_schema=schema)
        assert result.action == "deny"
        assert "URL" in result.reason or "url" in result.reason.lower()

    def test_url_field_without_network_none(self):
        schema = _schema({"endpoint": {"type": "string", "format": "uri"}})
        entry = _entry(permissions={"network_level": "restricted"})
        result = inspect_mcp_args("p", "t", {"endpoint": "https://example.com"}, entry, input_schema=schema)
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# 11. Technical deny overrides schema prompt
# ---------------------------------------------------------------------------

class TestDenyOverridesPrompt:
    def test_path_traversal_with_schema_violation(self):
        schema = _schema(
            {"path": {"type": "string"}, "count": {"type": "integer"}},
            required=["count"],
        )
        result = inspect_mcp_args(
            "p", "t", {"path": "../../../etc/passwd"}, _entry(), input_schema=schema,
        )
        assert result.action == "deny"
        assert "Path traversal" in result.reason

    def test_oversized_payload_with_schema_violation(self):
        schema = _schema({"name": {"type": "string"}}, required=["name"])
        huge = {"data": "x" * 2_000_000}
        result = inspect_mcp_args("p", "t", huge, _entry(), input_schema=schema)
        assert result.action == "deny"
        assert "Oversized" in result.reason


# ---------------------------------------------------------------------------
# 12. No external dependency added
# ---------------------------------------------------------------------------

class TestNoExternalDependency:
    def test_no_jsonschema_import(self):
        import agentnode_sdk.guard as guard_mod
        import sys
        assert "jsonschema" not in sys.modules


# ---------------------------------------------------------------------------
# 13. _safe_schema unit tests
# ---------------------------------------------------------------------------

class TestSafeSchema:
    def test_none_returns_none(self):
        assert _safe_schema(None) is None

    def test_valid_schema_returned(self):
        s = {"type": "object", "properties": {"x": {"type": "string"}}}
        assert _safe_schema(s) is s

    def test_no_type_accepted(self):
        s = {"properties": {"x": {"type": "string"}}}
        assert _safe_schema(s) is s

    def test_non_dict_returns_none(self):
        assert _safe_schema([1, 2]) is None

    def test_non_object_type_returns_none(self):
        assert _safe_schema({"type": "array"}) is None

    def test_bad_properties_returns_none(self):
        assert _safe_schema({"type": "object", "properties": "bad"}) is None


# ---------------------------------------------------------------------------
# 14. _validate_field unit tests
# ---------------------------------------------------------------------------

class TestValidateField:
    def test_null_non_nullable(self):
        violations = _validate_field("x", None, {"type": "string"})
        assert any("Null" in v for v in violations)

    def test_null_nullable(self):
        violations = _validate_field("x", None, {"type": "null"})
        assert len(violations) == 0

    def test_bool_not_treated_as_number(self):
        violations = _validate_field("flag", True, {"type": "integer", "minimum": 0})
        assert any("Type mismatch" in v for v in violations)


# ---------------------------------------------------------------------------
# 15. Multiple schema violations aggregated
# ---------------------------------------------------------------------------

class TestMultipleViolations:
    def test_multiple_violations_in_reason(self):
        schema = _schema(
            {"name": {"type": "string", "minLength": 5}, "count": {"type": "integer"}},
            required=["name", "count"],
        )
        result = inspect_mcp_args("p", "t", {"name": "ab", "count": "bad"}, _entry(), input_schema=schema)
        assert result.action == "prompt"
        assert "too short" in result.reason
        assert "Type mismatch" in result.reason
