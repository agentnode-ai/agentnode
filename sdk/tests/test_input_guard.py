"""Tests for input_guard — tool argument validation (warning layer)."""
import pytest

from agentnode_sdk.input_guard import validate_tool_input


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
        warnings = validate_tool_input(
            "test-pack", None,
            {"file_path": "../../etc/passwd"},
            _entry(),
        )
        assert len(warnings) == 1
        assert ".." in warnings[0]

    def test_traversal_backslash(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"path": "..\\windows\\system32"},
            _entry(),
        )
        assert any(".." in w for w in warnings)

    def test_normal_path_ok(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"file_path": "report.pdf"},
            _entry(),
        )
        assert warnings == []

    def test_relative_path_without_traversal(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"file_path": "./data/file.csv"},
            _entry(),
        )
        assert warnings == []


class TestOversizedInputs:
    def test_oversized_string(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"text": "x" * 2_000_000},
            _entry(),
        )
        assert len(warnings) == 1
        assert "MB" in warnings[0]

    def test_normal_string_ok(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"text": "hello world"},
            _entry(),
        )
        assert warnings == []

    def test_oversized_list(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"items": list(range(20_000))},
            _entry(),
        )
        assert len(warnings) == 1
        assert "20000" in warnings[0]

    def test_oversized_dict(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"data": {str(i): i for i in range(15_000)}},
            _entry(),
        )
        assert len(warnings) == 1
        assert "15000" in warnings[0]

    def test_normal_collection_ok(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"items": list(range(100))},
            _entry(),
        )
        assert warnings == []


class TestNetworkMismatch:
    def test_url_with_no_network(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"target": "https://example.com/api"},
            _entry(network="none"),
        )
        assert len(warnings) == 1
        assert "URL" in warnings[0]
        assert "network_level=none" in warnings[0]

    def test_url_with_network_allowed(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"target": "https://example.com/api"},
            _entry(network="external"),
        )
        assert warnings == []

    def test_non_url_string_ok(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"text": "just some text"},
            _entry(network="none"),
        )
        assert warnings == []


class TestEdgeCases:
    def test_empty_kwargs(self):
        warnings = validate_tool_input("test-pack", None, {}, _entry())
        assert warnings == []

    def test_no_permissions_in_entry(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {"file_path": "../../etc/passwd"},
            {},
        )
        assert len(warnings) == 1

    def test_multiple_warnings(self):
        warnings = validate_tool_input(
            "test-pack", None,
            {
                "path": "../../secret",
                "url": "https://evil.com",
                "big": "x" * 2_000_000,
            },
            _entry(network="none"),
        )
        assert len(warnings) == 3
