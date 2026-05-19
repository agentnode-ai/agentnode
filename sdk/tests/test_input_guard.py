"""Tests for input_guard — tool argument validation with escalation levels."""
import pytest

from agentnode_sdk.input_guard import InputFinding, validate_tool_input


def _entry(*, network="none", filesystem="none", code_execution="none"):
    return {
        "permissions": {
            "network_level": network,
            "filesystem_level": filesystem,
            "code_execution_level": code_execution,
        },
    }


class TestPathTraversal:
    def test_traversal_detected(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"file_path": "../../etc/passwd"},
            _entry(),
        )
        assert len(findings) == 1
        assert ".." in findings[0].message

    def test_traversal_is_prompt_level(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"file_path": "../../etc/passwd"},
            _entry(),
        )
        assert findings[0].level == "prompt"
        assert findings[0].code == "path_traversal"

    def test_traversal_backslash(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"path": "..\\windows\\system32"},
            _entry(),
        )
        assert any(".." in f.message for f in findings)
        assert any(f.code == "path_traversal" for f in findings)

    def test_normal_path_ok(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"file_path": "report.pdf"},
            _entry(),
        )
        assert findings == []

    def test_relative_path_without_traversal(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"file_path": "./data/file.csv"},
            _entry(),
        )
        assert findings == []


class TestOversizedInputs:
    def test_oversized_string(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"text": "x" * 2_000_000},
            _entry(),
        )
        assert len(findings) == 1
        assert "MB" in findings[0].message

    def test_oversized_string_is_warning_level(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"text": "x" * 2_000_000},
            _entry(),
        )
        assert findings[0].level == "warning"
        assert findings[0].code == "oversized_string"

    def test_normal_string_ok(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"text": "hello world"},
            _entry(),
        )
        assert findings == []

    def test_oversized_list(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"items": list(range(20_000))},
            _entry(),
        )
        assert len(findings) == 1
        assert "20000" in findings[0].message

    def test_oversized_list_is_warning_level(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"items": list(range(20_000))},
            _entry(),
        )
        assert findings[0].level == "warning"
        assert findings[0].code == "oversized_collection"

    def test_oversized_dict(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"data": {str(i): i for i in range(15_000)}},
            _entry(),
        )
        assert len(findings) == 1
        assert "15000" in findings[0].message

    def test_normal_collection_ok(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"items": list(range(100))},
            _entry(),
        )
        assert findings == []


class TestNetworkMismatch:
    def test_url_with_no_network(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"target": "https://example.com/api"},
            _entry(network="none"),
        )
        assert len(findings) == 1
        assert "URL" in findings[0].message
        assert "network_level=none" in findings[0].message

    def test_url_anomaly_is_prompt_level(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"target": "https://example.com/api"},
            _entry(network="none"),
        )
        assert findings[0].level == "prompt"
        assert findings[0].code == "url_anomaly"

    def test_url_with_network_allowed(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"target": "https://example.com/api"},
            _entry(network="external"),
        )
        assert findings == []

    def test_non_url_string_ok(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"text": "just some text"},
            _entry(network="none"),
        )
        assert findings == []


class TestEdgeCases:
    def test_empty_kwargs(self):
        findings = validate_tool_input("test-pack", None, {}, _entry())
        assert findings == []

    def test_no_permissions_in_entry(self):
        findings = validate_tool_input(
            "test-pack", None,
            {"file_path": "../../etc/passwd"},
            {},
        )
        assert len(findings) == 1

    def test_multiple_findings(self):
        findings = validate_tool_input(
            "test-pack", None,
            {
                "path": "../../secret",
                "url": "https://evil.com",
                "big": "x" * 2_000_000,
            },
            _entry(network="none"),
        )
        assert len(findings) == 3

    def test_multiple_findings_mixed_levels(self):
        findings = validate_tool_input(
            "test-pack", None,
            {
                "path": "../../secret",
                "big": "x" * 2_000_000,
            },
            _entry(network="none"),
        )
        prompt_findings = [f for f in findings if f.level == "prompt"]
        warning_findings = [f for f in findings if f.level == "warning"]
        assert len(prompt_findings) == 1
        assert len(warning_findings) == 1

    def test_str_representation(self):
        """InputFinding.__str__ returns the message for backward compat."""
        findings = validate_tool_input(
            "test-pack", None,
            {"file_path": "../../etc/passwd"},
            _entry(),
        )
        assert str(findings[0]) == findings[0].message
        assert ".." in str(findings[0])
