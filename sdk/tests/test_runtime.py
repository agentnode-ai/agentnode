"""Tests for agentnode_sdk.runtime — AgentNodeRuntime LLM integration layer."""
from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from agentnode_sdk.runtime import (
    AgentNodeRuntime,
    ToolError,
    ToolResult,
    ToolSpec,
    _result_to_dict,
)
from agentnode_sdk.policy import _trust_meets_minimum as trust_allows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(client=None, minimum_trust_level="verified"):
    """Create a runtime with a mock client."""
    mock_client = client or MagicMock()
    return AgentNodeRuntime(client=mock_client, minimum_trust_level=minimum_trust_level)


def _mock_search_result(hits=None, total=0):
    """Create a mock SearchResult."""
    sr = MagicMock()
    sr.hits = hits or []
    sr.total = total
    return sr


def _mock_search_hit(slug="test-pack", name="Test Pack", summary="A test",
                      trust_level="verified", latest_version="1.0.0",
                      download_count=100, capability_ids=None):
    """Create a mock SearchHit."""
    hit = MagicMock()
    hit.slug = slug
    hit.name = name
    hit.summary = summary
    hit.trust_level = trust_level
    hit.latest_version = latest_version
    hit.download_count = download_count
    hit.capability_ids = capability_ids or []
    return hit


def _mock_install_result(slug="test-pack", version="1.0.0", installed=True,
                          message="Installed", trust_level="verified"):
    """Create a mock InstallResult."""
    ir = MagicMock()
    ir.slug = slug
    ir.version = version
    ir.installed = installed
    ir.message = message
    ir.trust_level = trust_level
    return ir


def _mock_run_tool_result(success=True, result=None, error=None, duration_ms=42.0):
    """Create a mock RunToolResult."""
    rtr = MagicMock()
    rtr.success = success
    rtr.result = result
    rtr.error = error
    rtr.duration_ms = duration_ms
    return rtr


def _mock_resolve_result(total=3):
    """Create a mock ResolveResult."""
    rr = MagicMock()
    rr.total = total
    return rr


def _lockfile_with(packages=None):
    """Create a lockfile dict.

    Entries should include trust_level and permissions to pass policy checks.
    Use _pkg() to create well-formed entries.
    """
    return {
        "lockfile_version": "0.1",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "packages": packages or {},
    }


def _pkg(version="1.0", trust_level="verified", tools=None, **extra):
    """Build a lockfile package entry with policy-valid defaults."""
    entry = {
        "version": version,
        "trust_level": trust_level,
        "permissions": {"network_level": "none", "filesystem_level": "none",
                        "code_execution_level": "none"},
        "tools": tools or [],
    }
    entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# TestToolSpecs
# ---------------------------------------------------------------------------

class TestToolSpecs:
    def test_returns_five_specs(self):
        rt = _make_runtime()
        specs = rt.tool_specs()
        assert len(specs) == 5

    def test_all_are_toolspec_instances(self):
        rt = _make_runtime()
        for spec in rt.tool_specs():
            assert isinstance(spec, ToolSpec)

    def test_expected_names(self):
        rt = _make_runtime()
        names = [s.name for s in rt.tool_specs()]
        assert names == [
            "agentnode_capabilities",
            "agentnode_search",
            "agentnode_install",
            "agentnode_run",
            "agentnode_acquire",
        ]

    def test_all_have_description(self):
        rt = _make_runtime()
        for spec in rt.tool_specs():
            assert isinstance(spec.description, str)
            assert len(spec.description) > 10

    def test_all_have_valid_input_schema(self):
        rt = _make_runtime()
        for spec in rt.tool_specs():
            schema = spec.input_schema
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema

    def test_returns_copies(self):
        rt = _make_runtime()
        a = rt.tool_specs()
        b = rt.tool_specs()
        assert a is not b


# ---------------------------------------------------------------------------
# TestFormatConversions
# ---------------------------------------------------------------------------

class TestFormatConversions:
    def test_openai_maps_input_schema_to_parameters(self):
        rt = _make_runtime()
        tools = rt.as_openai_tools()
        for tool in tools:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert "input_schema" not in fn

    def test_anthropic_keeps_input_schema(self):
        rt = _make_runtime()
        tools = rt.as_anthropic_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "parameters" not in tool

    def test_generic_keeps_input_schema(self):
        rt = _make_runtime()
        tools = rt.as_generic_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "parameters" not in tool

    def test_openai_count_matches_specs(self):
        rt = _make_runtime()
        assert len(rt.as_openai_tools()) == len(rt.tool_specs())

    def test_anthropic_count_matches_specs(self):
        rt = _make_runtime()
        assert len(rt.as_anthropic_tools()) == len(rt.tool_specs())

    def test_generic_count_matches_specs(self):
        rt = _make_runtime()
        assert len(rt.as_generic_tools()) == len(rt.tool_specs())

    def test_openai_schema_equals_spec_input_schema(self):
        rt = _make_runtime()
        specs = rt.tool_specs()
        tools = rt.as_openai_tools()
        for spec, tool in zip(specs, tools):
            assert tool["function"]["parameters"] == spec.input_schema

    def test_anthropic_schema_equals_spec_input_schema(self):
        rt = _make_runtime()
        specs = rt.tool_specs()
        tools = rt.as_anthropic_tools()
        for spec, tool in zip(specs, tools):
            assert tool["input_schema"] == spec.input_schema


# ---------------------------------------------------------------------------
# TestToolBundle
# ---------------------------------------------------------------------------

class TestToolBundle:
    def test_contains_tools_and_system_prompt(self):
        rt = _make_runtime()
        bundle = rt.tool_bundle()
        assert "tools" in bundle
        assert "system_prompt" in bundle

    def test_tools_is_generic_format(self):
        rt = _make_runtime()
        bundle = rt.tool_bundle()
        assert bundle["tools"] == rt.as_generic_tools()

    def test_system_prompt_matches(self):
        rt = _make_runtime()
        bundle = rt.tool_bundle()
        assert bundle["system_prompt"] == rt.system_prompt()

    def test_tools_is_list(self):
        rt = _make_runtime()
        bundle = rt.tool_bundle()
        assert isinstance(bundle["tools"], list)

    def test_system_prompt_is_string(self):
        rt = _make_runtime()
        bundle = rt.tool_bundle()
        assert isinstance(bundle["system_prompt"], str)


# ---------------------------------------------------------------------------
# TestSystemPrompt
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_under_1200_chars(self):
        rt = _make_runtime()
        assert len(rt.system_prompt()) < 1200

    def test_contains_key_rules(self):
        rt = _make_runtime()
        prompt = rt.system_prompt()
        assert "AgentNode" in prompt
        assert "installed" in prompt
        assert "invent" in prompt
        assert "repeat" in prompt.lower()

    def test_no_marketing(self):
        rt = _make_runtime()
        prompt = rt.system_prompt().lower()
        for word in ["revolutionary", "amazing", "best", "powerful", "exciting"]:
            assert word not in prompt


# ---------------------------------------------------------------------------
# TestHandleRouting
# ---------------------------------------------------------------------------

class TestHandleRouting:
    @patch("agentnode_sdk.runtime.read_lockfile", return_value=_lockfile_with())
    def test_routes_capabilities(self, _mock_lf):
        rt = _make_runtime()
        result = rt.handle("agentnode_capabilities")
        assert result["success"] is True

    def test_routes_search(self):
        mock_client = MagicMock()
        mock_client.search.return_value = _mock_search_result()
        rt = _make_runtime(client=mock_client)
        result = rt.handle("agentnode_search", {"query": "pdf"})
        assert result["success"] is True

    def test_routes_install(self):
        mock_client = MagicMock()
        mock_client.install.return_value = _mock_install_result()
        rt = _make_runtime(client=mock_client)
        result = rt.handle("agentnode_install", {"slug": "test-pack"})
        assert result["success"] is True

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_routes_run(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "test-pack": _pkg(tools=[{"name": "run"}]),
        })
        mock_client = MagicMock()
        mock_client.run_tool.return_value = _mock_run_tool_result(result={"ok": True})
        rt = _make_runtime(client=mock_client)
        result = rt.handle("agentnode_run", {"slug": "test-pack"})
        assert result["success"] is True

    def test_routes_acquire(self):
        mock_client = MagicMock()
        mock_client.resolve_and_install.return_value = _mock_install_result()
        mock_client.resolve.return_value = _mock_resolve_result(total=3)
        rt = _make_runtime(client=mock_client)
        result = rt.handle("agentnode_acquire", {"capability": "pdf"})
        assert result["success"] is True

    def test_unknown_tool_returns_error(self):
        rt = _make_runtime()
        result = rt.handle("nonexistent_tool")
        assert result["success"] is False
        assert result["error"]["code"] == "unknown_tool"

    def test_none_args_defaults_to_empty(self):
        rt = _make_runtime()
        # agentnode_capabilities works with no args
        with patch("agentnode_sdk.runtime.read_lockfile", return_value=_lockfile_with()):
            result = rt.handle("agentnode_capabilities", None)
            assert result["success"] is True


# ---------------------------------------------------------------------------
# TestResponseFormat
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Every handler returns success+result or success=false+error.code+message."""

    @patch("agentnode_sdk.runtime.read_lockfile", return_value=_lockfile_with())
    def test_capabilities_success_format(self, _):
        rt = _make_runtime()
        result = rt.handle("agentnode_capabilities")
        assert "success" in result
        assert result["success"] is True
        assert "result" in result

    def test_search_success_format(self):
        mock_client = MagicMock()
        mock_client.search.return_value = _mock_search_result()
        rt = _make_runtime(client=mock_client)
        result = rt.handle("agentnode_search", {"query": "test"})
        assert result["success"] is True
        assert "result" in result

    def test_search_error_format(self):
        rt = _make_runtime()
        result = rt.handle("agentnode_search", {})
        assert result["success"] is False
        assert "error" in result
        assert "code" in result["error"]
        assert "message" in result["error"]

    def test_install_error_format(self):
        rt = _make_runtime()
        result = rt.handle("agentnode_install", {})
        assert result["success"] is False
        assert result["error"]["code"] == "missing_parameter"

    @patch("agentnode_sdk.runtime.read_lockfile", return_value=_lockfile_with())
    def test_run_error_format(self, _):
        rt = _make_runtime()
        result = rt.handle("agentnode_run", {"slug": "missing"})
        assert result["success"] is False
        assert result["error"]["code"] == "not_installed"

    def test_acquire_error_format(self):
        rt = _make_runtime()
        result = rt.handle("agentnode_acquire", {})
        assert result["success"] is False
        assert result["error"]["code"] == "missing_parameter"


# ---------------------------------------------------------------------------
# TestHandleCapabilities
# ---------------------------------------------------------------------------

class TestHandleCapabilities:
    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_reads_lockfile(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "pdf-reader": {
                "version": "1.0.0",
                "trust_level": "verified",
                "tools": [{"name": "extract_text"}],
                "capability_ids": ["pdf_extraction"],
            },
        })
        rt = _make_runtime()
        result = rt.handle("agentnode_capabilities")
        assert result["success"] is True
        assert result["result"]["installed_count"] == 1
        pkg = result["result"]["packages"][0]
        assert pkg["slug"] == "pdf-reader"
        assert pkg["version"] == "1.0.0"
        assert pkg["trust_level"] == "verified"
        assert pkg["tools"] == ["extract_text"]
        assert pkg["capability_ids"] == ["pdf_extraction"]

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_empty_lockfile(self, mock_lf):
        mock_lf.return_value = _lockfile_with()
        rt = _make_runtime()
        result = rt.handle("agentnode_capabilities")
        assert result["success"] is True
        assert result["result"]["installed_count"] == 0
        assert result["result"]["packages"] == []

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_multiple_packages(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "pack-a": {"version": "1.0", "trust_level": "trusted", "tools": []},
            "pack-b": {"version": "2.0", "trust_level": "curated", "tools": []},
        })
        rt = _make_runtime()
        result = rt.handle("agentnode_capabilities")
        assert result["result"]["installed_count"] == 2

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_purely_local(self, mock_lf):
        """Capabilities handler must not call the API client."""
        mock_lf.return_value = _lockfile_with()
        mock_client = MagicMock()
        rt = _make_runtime(client=mock_client)
        rt.handle("agentnode_capabilities")
        mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# TestHandleSearch
# ---------------------------------------------------------------------------

class TestHandleSearch:
    def test_delegates_to_client(self):
        hit = _mock_search_hit()
        mock_client = MagicMock()
        mock_client.search.return_value = _mock_search_result(hits=[hit], total=1)
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_search", {"query": "pdf"})
        mock_client.search.assert_called_once_with(query="pdf", per_page=5)
        assert result["success"] is True
        assert result["result"]["total"] == 1
        assert len(result["result"]["results"]) == 1

    def test_max_five_results(self):
        hits = [_mock_search_hit(slug=f"pack-{i}") for i in range(10)]
        mock_client = MagicMock()
        mock_client.search.return_value = _mock_search_result(hits=hits, total=10)
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_search", {"query": "test"})
        assert len(result["result"]["results"]) == 5

    def test_api_error_returns_error_dict(self):
        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("Network timeout")
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_search", {"query": "pdf"})
        assert result["success"] is False
        assert result["error"]["code"] == "internal_error"
        assert "Network timeout" in result["error"]["message"]

    def test_missing_query(self):
        rt = _make_runtime()
        result = rt.handle("agentnode_search", {})
        assert result["success"] is False
        assert result["error"]["code"] == "missing_parameter"


# ---------------------------------------------------------------------------
# TestHandleInstall
# ---------------------------------------------------------------------------

class TestHandleInstall:
    def test_delegates_to_client(self):
        mock_client = MagicMock()
        mock_client.install.return_value = _mock_install_result()
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_install", {"slug": "test-pack"})
        assert result["success"] is True
        assert result["result"]["slug"] == "test-pack"
        mock_client.install.assert_called_once()

    def test_trust_flows_through_verified(self):
        mock_client = MagicMock()
        mock_client.install.return_value = _mock_install_result()
        rt = _make_runtime(client=mock_client, minimum_trust_level="verified")

        rt.handle("agentnode_install", {"slug": "test-pack"})
        _, kwargs = mock_client.install.call_args
        assert kwargs["require_verified"] is True
        assert kwargs["require_trusted"] is False

    def test_trust_flows_through_trusted(self):
        mock_client = MagicMock()
        mock_client.install.return_value = _mock_install_result()
        rt = _make_runtime(client=mock_client, minimum_trust_level="trusted")

        rt.handle("agentnode_install", {"slug": "test-pack"})
        _, kwargs = mock_client.install.call_args
        assert kwargs["require_trusted"] is True
        assert kwargs["require_verified"] is False

    def test_install_failed(self):
        mock_client = MagicMock()
        mock_client.install.return_value = _mock_install_result(
            installed=False, message="Trust check failed"
        )
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_install", {"slug": "bad-pack"})
        assert result["success"] is False
        assert result["error"]["code"] == "install_failed"

    def test_missing_slug(self):
        rt = _make_runtime()
        result = rt.handle("agentnode_install", {})
        assert result["success"] is False
        assert result["error"]["code"] == "missing_parameter"


# ---------------------------------------------------------------------------
# TestHandleRun
# ---------------------------------------------------------------------------

class TestHandleRun:
    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_slug_tool_name_arguments(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "my-pack": _pkg(tools=[{"name": "tool_a"}, {"name": "tool_b"}]),
        })
        mock_client = MagicMock()
        mock_client.run_tool.return_value = _mock_run_tool_result(result={"answer": 42})
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_run", {
            "slug": "my-pack",
            "tool_name": "tool_a",
            "arguments": {"x": 1},
        })
        assert result["success"] is True
        assert result["result"]["output"] == {"answer": 42}
        assert result["result"]["duration_ms"] == 42.0
        mock_client.run_tool.assert_called_once_with("my-pack", "tool_a", x=1)

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_multi_tool_error_with_available_tools(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "multi-pack": _pkg(tools=[{"name": "extract_text"}, {"name": "summarize_pdf"}]),
        })
        rt = _make_runtime()

        result = rt.handle("agentnode_run", {"slug": "multi-pack"})
        assert result["success"] is False
        assert result["error"]["code"] == "tool_name_required"
        assert result["available_tools"] == ["extract_text", "summarize_pdf"]

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_single_tool_auto_select(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "single-pack": _pkg(tools=[{"name": "the_tool"}]),
        })
        mock_client = MagicMock()
        mock_client.run_tool.return_value = _mock_run_tool_result(result="ok")
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_run", {"slug": "single-pack"})
        assert result["success"] is True
        mock_client.run_tool.assert_called_once_with("single-pack", "the_tool")

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_args_default_empty(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "pack": _pkg(tools=[{"name": "t"}]),
        })
        mock_client = MagicMock()
        mock_client.run_tool.return_value = _mock_run_tool_result(result="ok")
        rt = _make_runtime(client=mock_client)

        rt.handle("agentnode_run", {"slug": "pack"})
        # No extra kwargs (arguments={})
        mock_client.run_tool.assert_called_once_with("pack", "t")

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_package_not_installed(self, mock_lf):
        mock_lf.return_value = _lockfile_with()
        rt = _make_runtime()
        result = rt.handle("agentnode_run", {"slug": "missing"})
        assert result["success"] is False
        assert result["error"]["code"] == "not_installed"
        assert "agentnode_install" in result["error"]["message"]

    def test_missing_slug(self):
        rt = _make_runtime()
        result = rt.handle("agentnode_run", {})
        assert result["success"] is False
        assert result["error"]["code"] == "missing_parameter"

    @patch("agentnode_sdk.runtime.read_lockfile")
    def test_run_failure(self, mock_lf):
        mock_lf.return_value = _lockfile_with({
            "pack": _pkg(tools=[{"name": "t"}]),
        })
        mock_client = MagicMock()
        mock_client.run_tool.return_value = _mock_run_tool_result(
            success=False, error="Timeout"
        )
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_run", {"slug": "pack"})
        assert result["success"] is False
        assert result["error"]["code"] == "run_failed"


# ---------------------------------------------------------------------------
# TestHandleAcquire
# ---------------------------------------------------------------------------

class TestHandleAcquire:
    def test_resolve_and_install(self):
        mock_client = MagicMock()
        mock_client.resolve_and_install.return_value = _mock_install_result(
            slug="pdf-reader", version="1.2.0", trust_level="verified"
        )
        mock_client.resolve.return_value = _mock_resolve_result(total=5)
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_acquire", {"capability": "pdf_extraction"})
        assert result["success"] is True
        selected = result["result"]["selected"]
        assert selected["slug"] == "pdf-reader"
        assert selected["version"] == "1.2.0"
        assert selected["trust_level"] == "verified"

    def test_alternatives_count(self):
        mock_client = MagicMock()
        mock_client.resolve_and_install.return_value = _mock_install_result()
        mock_client.resolve.return_value = _mock_resolve_result(total=5)
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_acquire", {"capability": "pdf"})
        assert result["result"]["alternatives_count"] == 4

    def test_no_match_error(self):
        mock_client = MagicMock()
        mock_client.resolve_and_install.return_value = _mock_install_result(
            installed=False, message="No packages found"
        )
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_acquire", {"capability": "nonexistent"})
        assert result["success"] is False
        assert result["error"]["code"] == "acquire_failed"

    def test_missing_capability(self):
        rt = _make_runtime()
        result = rt.handle("agentnode_acquire", {})
        assert result["success"] is False
        assert result["error"]["code"] == "missing_parameter"


# ---------------------------------------------------------------------------
# TestTrustAllows
# ---------------------------------------------------------------------------

class TestTrustAllows:
    """All 3x3 combinations + unknown trust."""

    # minimum = verified: accepts verified, trusted, curated
    def test_verified_allows_verified(self):
        assert trust_allows("verified", "verified") is True

    def test_verified_allows_trusted(self):
        assert trust_allows("trusted", "verified") is True

    def test_verified_allows_curated(self):
        assert trust_allows("curated", "verified") is True

    # minimum = trusted: accepts trusted, curated
    def test_trusted_rejects_verified(self):
        assert trust_allows("verified", "trusted") is False

    def test_trusted_allows_trusted(self):
        assert trust_allows("trusted", "trusted") is True

    def test_trusted_allows_curated(self):
        assert trust_allows("curated", "trusted") is True

    # minimum = curated: accepts only curated
    def test_curated_rejects_verified(self):
        assert trust_allows("verified", "curated") is False

    def test_curated_rejects_trusted(self):
        assert trust_allows("trusted", "curated") is False

    def test_curated_allows_curated(self):
        assert trust_allows("curated", "curated") is True

    # unknown trust → reject
    def test_unknown_trust_rejected(self):
        assert trust_allows("unverified", "verified") is False

    def test_empty_trust_rejected(self):
        assert trust_allows("", "verified") is False

    def test_nonsense_trust_rejected(self):
        assert trust_allows("super_trusted", "verified") is False


# ---------------------------------------------------------------------------
# TestMetadataSerialization
# ---------------------------------------------------------------------------

class TestMetadataSerialization:
    def test_metadata_merged_to_top_level(self):
        result = ToolResult(
            success=False,
            error=ToolError(code="tool_name_required", message="Multiple tools."),
            metadata={"available_tools": ["a", "b"]},
        )
        d = _result_to_dict(result)
        assert d["available_tools"] == ["a", "b"]
        assert d["success"] is False
        assert d["error"]["code"] == "tool_name_required"

    def test_none_metadata_no_extra_fields(self):
        result = ToolResult(success=True, result={"data": 1})
        d = _result_to_dict(result)
        assert d == {"success": True, "result": {"data": 1}}
        assert "metadata" not in d

    def test_empty_metadata_no_extra_fields(self):
        result = ToolResult(success=True, result={"data": 1}, metadata={})
        d = _result_to_dict(result)
        assert "available_tools" not in d

    def test_multiple_metadata_keys(self):
        result = ToolResult(
            success=True,
            result={"x": 1},
            metadata={"warnings": ["low battery"], "available_tools": ["t"]},
        )
        d = _result_to_dict(result)
        assert d["warnings"] == ["low battery"]
        assert d["available_tools"] == ["t"]


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_handle_never_throws_on_handler_exception(self):
        mock_client = MagicMock()
        mock_client.search.side_effect = RuntimeError("boom")
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_search", {"query": "test"})
        assert result["success"] is False
        assert result["error"]["code"] == "internal_error"
        assert "boom" in result["error"]["message"]

    def test_handle_never_throws_on_unknown_tool(self):
        rt = _make_runtime()
        result = rt.handle("totally_invalid_tool_name")
        assert result["success"] is False

    def test_run_never_throws_on_unknown_provider(self):
        rt = _make_runtime()
        result = rt.run(
            provider="unknown",
            client=MagicMock(),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert isinstance(result, dict)
        assert result["success"] is False

    def test_run_never_throws_on_loop_error(self):
        rt = _make_runtime()
        mock_llm_client = MagicMock()
        mock_llm_client.chat.completions.create.side_effect = RuntimeError("API down")
        result = rt.run(
            provider="openai",
            client=mock_llm_client,
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4",
        )
        assert isinstance(result, dict)
        assert result["success"] is False
        assert result["error"]["code"] == "loop_error"

    def test_handle_network_error_readable(self):
        mock_client = MagicMock()
        mock_client.install.side_effect = ConnectionError("Connection refused")
        rt = _make_runtime(client=mock_client)

        result = rt.handle("agentnode_install", {"slug": "test"})
        assert result["success"] is False
        assert "Connection refused" in result["error"]["message"]

    def test_all_results_json_serializable(self):
        """All handle() results must be JSON-serializable."""
        rt = _make_runtime()
        with patch("agentnode_sdk.runtime.read_lockfile", return_value=_lockfile_with()):
            result = rt.handle("agentnode_capabilities")
            json.dumps(result)  # Should not raise

        result = rt.handle("nonexistent")
        json.dumps(result)  # Should not raise


# ---------------------------------------------------------------------------
# TestRunUnknownProvider
# ---------------------------------------------------------------------------

class TestRunUnknownProvider:
    def test_returns_structured_error(self):
        rt = _make_runtime()
        result = rt.run(
            provider="foobar",
            client=MagicMock(),
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result["success"] is False
        assert result["error"]["code"] == "unknown_provider"
        assert "foobar" in result["error"]["message"]

    def test_does_not_raise(self):
        rt = _make_runtime()
        # This should return a dict, not raise
        result = rt.run(
            provider="deepseek",
            client=None,
            messages=[],
        )
        assert isinstance(result, dict)

    def test_suggests_valid_providers(self):
        rt = _make_runtime()
        result = rt.run(
            provider="mistral",
            client=MagicMock(),
            messages=[],
        )
        msg = result["error"]["message"]
        assert "openai" in msg
        assert "anthropic" in msg
        assert "gemini" in msg


# ---------------------------------------------------------------------------
# TestRunSystemPromptInjection
# ---------------------------------------------------------------------------

class TestRunSystemPromptInjection:
    def test_appends_to_existing_system(self):
        rt = _make_runtime()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
        # Use unknown provider to test injection without needing loop
        rt.run(provider="__test__", client=None, messages=messages)
        assert "AgentNode" in messages[0]["content"]
        assert messages[0]["content"].startswith("You are helpful.")

    def test_creates_system_if_missing(self):
        rt = _make_runtime()
        messages = [{"role": "user", "content": "hi"}]
        rt.run(provider="__test__", client=None, messages=messages)
        assert messages[0]["role"] == "system"
        assert "AgentNode" in messages[0]["content"]

    def test_skip_injection(self):
        rt = _make_runtime()
        messages = [{"role": "user", "content": "hi"}]
        rt.run(
            provider="__test__",
            client=None,
            messages=messages,
            inject_system_prompt=False,
        )
        assert messages[0]["role"] == "user"
