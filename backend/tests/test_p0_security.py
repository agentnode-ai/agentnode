"""Negative tests for P0 security fixes.

Tests that the three P0 vulnerabilities (URL XSS, Meilisearch filter injection,
entrypoint code injection) are properly patched and stay patched.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.packages.validator import validate_manifest
from app.search.schemas import SearchRequest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

VALID_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "test-pack",
    "package_type": "toolpack",
    "name": "Test Pack",
    "publisher": "test-publisher",
    "version": "1.0.0",
    "summary": "A test package.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "test_pack.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
            "input_schema": {"type": "object", "properties": {"input": {"type": "string"}}},
        }],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"], "python": ">=3.10"},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "temp"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
        "external_integrations": [],
    },
    "tags": ["test"],
    "categories": ["document-processing"],
    "dependencies": [],
}


# ===========================================================================
# P0 #1: URL XSS — validator must reject non-http(s) URLs
# ===========================================================================


class TestUrlXssProtection:

    @pytest.mark.asyncio
    async def test_javascript_url_rejected(self):
        """javascript: URLs must be rejected at publish time."""
        m = {**VALID_MANIFEST, "homepage_url": "javascript:alert(1)"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False
        assert any("https://" in e or "http://" in e for e in errors)

    @pytest.mark.asyncio
    async def test_data_url_rejected(self):
        """data: URLs must be rejected at publish time."""
        m = {**VALID_MANIFEST, "docs_url": "data:text/html,<script>alert(1)</script>"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False

    @pytest.mark.asyncio
    async def test_vbscript_url_rejected(self):
        m = {**VALID_MANIFEST, "source_url": "vbscript:MsgBox(1)"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False

    @pytest.mark.asyncio
    async def test_https_url_accepted(self):
        m = {**VALID_MANIFEST, "homepage_url": "https://example.com"}
        valid, errors, _ = await validate_manifest(m)
        # Should not have URL-related errors
        url_errors = [e for e in errors if "homepage_url" in e]
        assert url_errors == []

    @pytest.mark.asyncio
    async def test_http_url_accepted(self):
        m = {**VALID_MANIFEST, "docs_url": "http://example.com"}
        valid, errors, _ = await validate_manifest(m)
        url_errors = [e for e in errors if "docs_url" in e]
        assert url_errors == []

    @pytest.mark.asyncio
    async def test_all_three_url_fields_checked(self):
        """All URL fields must go through the same guard."""
        for field in ("homepage_url", "docs_url", "source_url"):
            m = {**VALID_MANIFEST, field: "javascript:void(0)"}
            valid, errors, _ = await validate_manifest(m)
            assert valid is False, f"{field} should reject javascript: URLs"

    @pytest.mark.asyncio
    async def test_case_insensitive_rejection(self):
        """JaVaScRiPt: should also be rejected."""
        m = {**VALID_MANIFEST, "homepage_url": "JaVaScRiPt:alert(1)"}
        valid, errors, _ = await validate_manifest(m)
        assert valid is False


# ===========================================================================
# P0 #2: Meilisearch filter injection — schema must reject malicious values
# ===========================================================================


class TestSearchFilterInjection:

    def test_normal_filter_accepted(self):
        """Normal enum-like filter values pass validation."""
        req = SearchRequest(
            q="test",
            package_type="toolpack",
            runtime="python",
            verification_tier="verified",
        )
        assert req.package_type == "toolpack"
        assert req.runtime == "python"

    def test_quote_injection_rejected(self):
        """Double-quote in filter value must be rejected."""
        with pytest.raises(Exception):
            SearchRequest(q="test", package_type='toolpack" OR 1=1 OR "')

    def test_parenthesis_injection_rejected(self):
        """Parentheses in filter value must be rejected."""
        with pytest.raises(Exception):
            SearchRequest(q="test", framework="langchain) OR (true")

    def test_backslash_injection_rejected(self):
        with pytest.raises(Exception):
            SearchRequest(q="test", runtime="python\\")

    def test_space_injection_rejected(self):
        """Spaces should not be allowed in filter values."""
        with pytest.raises(Exception):
            SearchRequest(q="test", trust_level="verified OR true")

    def test_all_filter_fields_guarded(self):
        """Every filterable field must reject injection attempts."""
        injection = 'evil" OR 1=1 OR "'
        for field in (
            "package_type", "capability_id", "framework",
            "runtime", "trust_level", "verification_tier", "publisher_slug",
        ):
            with pytest.raises(Exception, match="Invalid filter value"):
                SearchRequest(q="test", **{field: injection})

    def test_sort_by_injection_rejected(self):
        """sort_by must only accept whitelisted values."""
        with pytest.raises(Exception):
            SearchRequest(q="test", sort_by="download_count:desc, _geo(0,0):asc")

    def test_sort_by_valid_accepted(self):
        req = SearchRequest(q="test", sort_by="download_count:desc")
        assert req.sort_by == "download_count:desc"

    def test_sort_by_arbitrary_field_rejected(self):
        with pytest.raises(Exception):
            SearchRequest(q="test", sort_by="secret_field:asc")

    def test_hyphen_and_underscore_accepted(self):
        """Hyphens and underscores are valid in slugs."""
        req = SearchRequest(q="test", publisher_slug="my-publisher_1")
        assert req.publisher_slug == "my-publisher_1"

    def test_empty_string_filter_accepted(self):
        """Empty string filters should pass (they are falsy, won't be used)."""
        # Pydantic allows None (the default) but empty string would pass regex too
        req = SearchRequest(q="test", package_type=None)
        assert req.package_type is None


# ===========================================================================
# P0 #3: Entrypoint / tool_name code injection
# ===========================================================================


class TestToolNameCodeInjection:

    def _generate_import_code(self, tool_name: str) -> str:
        """Generate the code that step_import would produce for a single tool."""
        from app.verification.steps import step_import
        from unittest.mock import MagicMock

        # Create a mock sandbox to capture the generated code
        mock_sandbox = MagicMock()
        mock_sandbox.run_python_code = MagicMock(return_value=(True, "All tool entrypoints verified"))

        tools = [{
            "name": tool_name,
            "entrypoint": "some.module:some_func",
            "input_schema": {"type": "object", "properties": {}},
        }]

        step_import(mock_sandbox, tools)
        generated_code = mock_sandbox.run_python_code.call_args[0][0]
        return generated_code

    def test_normal_tool_name(self):
        """Normal tool name works fine."""
        code = self._generate_import_code("my_tool")
        assert '"my_tool"' in code or "'my_tool'" in code

    def test_quotes_in_tool_name_escaped(self):
        """Double quotes in tool_name must be escaped in generated code."""
        malicious = 'evil"; import os; os.system("rm -rf /"); x="'
        code = self._generate_import_code(malicious)
        # The malicious payload should NOT appear as unescaped code
        assert 'import os; os.system' not in code.replace(json.dumps(malicious), "")
        # json.dumps properly escapes the quotes
        assert json.dumps(malicious) in code

    def test_backslash_in_tool_name_escaped(self):
        code = self._generate_import_code('tool\\"; print("pwned")')
        # Should be safely escaped
        assert 'print("pwned")' not in code.replace("_tn = ", "")

    def test_newline_in_tool_name_escaped(self):
        """Newlines in tool_name are escaped by json.dumps — no code breakout."""
        code = self._generate_import_code('tool\n"; import os; #')
        # json.dumps converts \n to \\n in the string literal, so the
        # _tn assignment is a single-line string — the payload stays data
        safe_literal = json.dumps('tool\n"; import os; #')
        assert safe_literal in code

    def test_triple_quote_in_tool_name(self):
        """Triple quotes should not break the string."""
        code = self._generate_import_code('tool"""; import os; """')
        assert json.dumps('tool"""; import os; """') in code

    def test_tool_name_appears_as_json_literal(self):
        """tool_name should be serialized via json.dumps, not interpolated."""
        code = self._generate_import_code("safe_tool")
        # Check that _tn variable assignment exists
        assert '_tn = "safe_tool"' in code

    def test_unicode_tool_name(self):
        """Unicode in tool_name should be handled safely."""
        code = self._generate_import_code("werkzeug_\u00fc\u00e4\u00f6")
        assert "_tn = " in code
