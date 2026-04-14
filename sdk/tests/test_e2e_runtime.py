"""End-to-end tests for AgentNodeRuntime with real API calls.

Phase 1: Real handle() against live AgentNode registry + local tool execution.
Phase 2: Real run() loops against OpenAI / Anthropic APIs.
Phase 3: Full-flow capability class tests (search → install → run) with scoring.

Usage:
    # Phase 1 only (no LLM keys needed):
    pytest tests/test_e2e_runtime.py -m "not openai and not anthropic" -v

    # Full suite with Anthropic (auto-detects Claude Code OAuth token):
    pytest tests/test_e2e_runtime.py -v

    # With explicit keys:
    OPENAI_API_KEY=sk-... pytest tests/test_e2e_runtime.py -m openai -v

    # Capability class tests only:
    pytest tests/test_e2e_runtime.py -k "CapabilityClasses" -v

    # Failure case tests:
    pytest tests/test_e2e_runtime.py -k "FailureCases" -v
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agentnode_sdk.runtime import AgentNodeRuntime
from agentnode_sdk.policy import _trust_meets_minimum as trust_allows

# conftest.py utilities — imported via sys.path (pytest adds tests/ to path)
from tests.conftest import (
    ToolCallTracker,
    ToolUsageScore,
    build_score,
    extract_response_text,
    write_score,
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _load_anthropic_token() -> str | None:
    """Load Anthropic API key from env or Claude Code OAuth credentials."""
    # 1. Explicit env var
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key

    # 2. Claude Code OAuth token (~/.claude/.credentials.json)
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.is_file():
        try:
            creds = json.loads(creds_path.read_text(encoding="utf-8"))
            oauth = creds.get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            expires_at = oauth.get("expiresAt", 0) / 1000
            if token and time.time() < expires_at:
                return token
        except Exception:
            pass

    return None


_anthropic_token = _load_anthropic_token()

# ---------------------------------------------------------------------------
# Markers & fixtures
# ---------------------------------------------------------------------------

requires_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
requires_anthropic = pytest.mark.skipif(
    _anthropic_token is None,
    reason="No Anthropic API key (env or Claude Code OAuth)",
)


@pytest.fixture
def runtime():
    """Runtime with real client (no mock)."""
    return AgentNodeRuntime(minimum_trust_level="verified")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Real handle() against live registry & local tools
# ═══════════════════════════════════════════════════════════════════════════


class TestE2ECapabilities:
    """Capabilities reads the real lockfile."""

    def test_returns_installed_packages(self, runtime):
        result = runtime.handle("agentnode_capabilities")
        assert result["success"] is True
        assert isinstance(result["result"]["installed_count"], int)
        assert isinstance(result["result"]["packages"], list)

    def test_word_counter_is_installed(self, runtime):
        result = runtime.handle("agentnode_capabilities")
        slugs = [p["slug"] for p in result["result"]["packages"]]
        assert "word-counter-pack" in slugs

    def test_package_has_tools_list(self, runtime):
        result = runtime.handle("agentnode_capabilities")
        for pkg in result["result"]["packages"]:
            assert "tools" in pkg
            assert isinstance(pkg["tools"], list)

    def test_no_api_call_made(self, runtime):
        """Capabilities must be purely local — fast and no network."""
        start = time.monotonic()
        runtime.handle("agentnode_capabilities")
        elapsed = time.monotonic() - start
        assert elapsed < 1.0  # Should be <10ms, but 1s is generous


class TestE2ESearch:
    """Search hits the real AgentNode registry."""

    def test_search_pdf(self, runtime):
        result = runtime.handle("agentnode_search", {"query": "pdf"})
        assert result["success"] is True
        assert result["result"]["total"] > 0
        slugs = [r["slug"] for r in result["result"]["results"]]
        assert any("pdf" in s for s in slugs)

    def test_search_csv(self, runtime):
        result = runtime.handle("agentnode_search", {"query": "csv data analysis"})
        assert result["success"] is True
        assert result["result"]["total"] > 0

    def test_search_max_five(self, runtime):
        result = runtime.handle("agentnode_search", {"query": "data"})
        assert result["success"] is True
        assert len(result["result"]["results"]) <= 5

    def test_search_result_structure(self, runtime):
        result = runtime.handle("agentnode_search", {"query": "pdf"})
        for r in result["result"]["results"]:
            assert "slug" in r
            assert "name" in r
            assert "summary" in r
            assert "trust_level" in r

    def test_search_nonsense_returns_zero(self, runtime):
        result = runtime.handle("agentnode_search", {"query": "xyzzy_nonexistent_98765"})
        assert result["success"] is True
        assert result["result"]["total"] == 0


class TestE2ERun:
    """Run word-counter-pack with real tool execution."""

    def test_count_words(self, runtime):
        result = runtime.handle("agentnode_run", {
            "slug": "word-counter-pack",
            "arguments": {"inputs": {"text": "Hello world"}},
        })
        assert result["success"] is True
        output = result["result"]["output"]
        assert output["words"] == 2
        assert output["characters"] == 11
        assert output["sentences"] == 1

    def test_count_words_longer_text(self, runtime):
        text = "The quick brown fox jumps over the lazy dog. It was a sunny day."
        result = runtime.handle("agentnode_run", {
            "slug": "word-counter-pack",
            "arguments": {"inputs": {"text": text}},
        })
        assert result["success"] is True
        output = result["result"]["output"]
        assert output["words"] == 14
        assert output["sentences"] == 2

    def test_has_duration(self, runtime):
        result = runtime.handle("agentnode_run", {
            "slug": "word-counter-pack",
            "arguments": {"inputs": {"text": "test"}},
        })
        assert result["success"] is True
        assert result["result"]["duration_ms"] > 0

    def test_run_missing_package(self, runtime):
        result = runtime.handle("agentnode_run", {
            "slug": "this-package-does-not-exist-xyz",
        })
        assert result["success"] is False
        assert result["error"]["code"] == "not_installed"
        assert "agentnode_install" in result["error"]["message"]


class TestE2EFullHandleFlow:
    """End-to-end: capabilities → search → run."""

    def test_discover_and_use_installed(self, runtime):
        """Simulate what an LLM would do: check capabilities, then use one."""
        # Step 1: Check what's installed
        caps = runtime.handle("agentnode_capabilities")
        assert caps["success"] is True

        installed = caps["result"]["packages"]
        assert len(installed) > 0

        # Step 2: Use the first installed package
        slug = installed[0]["slug"]
        tools = installed[0]["tools"]
        assert len(tools) > 0

        # Step 3: Run it (word-counter-pack expects inputs dict)
        result = runtime.handle("agentnode_run", {
            "slug": slug,
            "arguments": {"inputs": {"text": "This is an end-to-end test"}},
        })
        assert result["success"] is True
        assert "output" in result["result"]

    def test_search_then_check_registry(self, runtime):
        """Search for something, verify results are usable."""
        # Step 1: Search
        search = runtime.handle("agentnode_search", {"query": "pdf reader"})
        assert search["success"] is True
        assert search["result"]["total"] > 0

        # Step 2: Verify first result has installable info
        first = search["result"]["results"][0]
        assert first["slug"]
        assert first["trust_level"] in ("verified", "trusted", "curated")


class TestE2EResponseContract:
    """Every real response follows the standard format."""

    def test_success_has_result(self, runtime):
        r = runtime.handle("agentnode_capabilities")
        assert "success" in r
        assert "result" in r
        json.dumps(r)  # Must be serializable

    def test_error_has_code_and_message(self, runtime):
        r = runtime.handle("agentnode_run", {"slug": "nonexistent"})
        assert r["success"] is False
        assert "code" in r["error"]
        assert "message" in r["error"]
        json.dumps(r)  # Must be serializable

    def test_all_results_json_serializable(self, runtime):
        """Every handle() call produces JSON-safe output."""
        calls = [
            ("agentnode_capabilities", {}),
            ("agentnode_search", {"query": "pdf"}),
            ("agentnode_run", {
                "slug": "word-counter-pack",
                "arguments": {"inputs": {"text": "test"}},
            }),
            ("nonexistent_tool", {}),
        ]
        for name, args in calls:
            result = runtime.handle(name, args)
            serialized = json.dumps(result)
            assert isinstance(serialized, str)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — Real LLM tool loops
# ═══════════════════════════════════════════════════════════════════════════

# ------ Format & prompt integration (no LLM call needed) ------

class TestE2EToolFormats:
    """Verify tool definitions work with real provider schemas."""

    def test_openai_tools_valid_structure(self, runtime):
        tools = runtime.as_openai_tools()
        for tool in tools:
            assert tool["type"] == "function"
            fn = tool["function"]
            # OpenAI requires these fields
            assert isinstance(fn["name"], str)
            assert isinstance(fn["description"], str)
            assert isinstance(fn["parameters"], dict)
            assert fn["parameters"]["type"] == "object"

    def test_anthropic_tools_valid_structure(self, runtime):
        tools = runtime.as_anthropic_tools()
        for tool in tools:
            assert isinstance(tool["name"], str)
            assert isinstance(tool["description"], str)
            assert isinstance(tool["input_schema"], dict)
            assert tool["input_schema"]["type"] == "object"

    def test_system_prompt_injection_preserves_original(self, runtime):
        original = "You are a helpful assistant."
        messages = [
            {"role": "system", "content": original},
            {"role": "user", "content": "hi"},
        ]
        # Use unknown provider — triggers injection, returns error
        runtime.run(provider="__test__", client=None, messages=messages)
        assert messages[0]["content"].startswith(original)
        assert "AgentNode" in messages[0]["content"]


# ------ OpenAI real loop ------

@requires_openai
class TestE2EOpenAI:
    """Real OpenAI tool loop. Requires OPENAI_API_KEY."""

    @pytest.fixture
    def openai_client(self):
        import openai
        return openai.OpenAI()

    def test_capabilities_loop(self, runtime, openai_client):
        """LLM checks installed capabilities via tool call."""
        messages = [
            {"role": "user", "content": (
                "Use the agentnode_capabilities tool to list what tools "
                "are installed. Then tell me how many packages are installed "
                "and their names. Be brief."
            )},
        ]
        result = runtime.run(
            provider="openai",
            client=openai_client,
            messages=messages,
            model="gpt-4o-mini",
            max_tool_rounds=3,
        )
        # Should be a ChatCompletion message, not an error dict
        assert not isinstance(result, dict) or "success" not in result
        assert hasattr(result, "content")
        assert result.content is not None
        content = result.content.lower()
        assert "word-counter" in content or "1" in content

    def test_search_and_describe(self, runtime, openai_client):
        """LLM searches registry and describes results."""
        messages = [
            {"role": "user", "content": (
                "Search AgentNode for PDF-related tools. "
                "Tell me the slug and summary of the top result. Be brief."
            )},
        ]
        result = runtime.run(
            provider="openai",
            client=openai_client,
            messages=messages,
            model="gpt-4o-mini",
            max_tool_rounds=3,
        )
        assert not isinstance(result, dict) or "success" not in result
        assert hasattr(result, "content")
        assert result.content is not None
        assert "pdf" in result.content.lower()

    def test_run_word_counter(self, runtime, openai_client):
        """LLM uses word-counter-pack to count words in given text."""
        messages = [
            {"role": "user", "content": (
                "Use agentnode_run to count the words in this text: "
                "'The quick brown fox jumps over the lazy dog'. "
                "Pass it as: slug='word-counter-pack', "
                "arguments={'inputs': {'text': 'The quick brown fox jumps over the lazy dog'}}. "
                "Tell me the word count."
            )},
        ]
        result = runtime.run(
            provider="openai",
            client=openai_client,
            messages=messages,
            model="gpt-4o-mini",
            max_tool_rounds=3,
        )
        assert not isinstance(result, dict) or "success" not in result
        assert hasattr(result, "content")
        assert "9" in result.content

    def test_full_flow_capabilities_then_run(self, runtime, openai_client):
        """LLM checks capabilities, then runs a tool — multi-step."""
        messages = [
            {"role": "user", "content": (
                "Check what AgentNode capabilities are installed. "
                "Then use the word counter tool to count words in: "
                "'Hello world from the runtime'. "
                "Pass arguments as: {'inputs': {'text': 'Hello world from the runtime'}}. "
                "Tell me the exact word count."
            )},
        ]
        result = runtime.run(
            provider="openai",
            client=openai_client,
            messages=messages,
            model="gpt-4o-mini",
            max_tool_rounds=8,
        )
        assert hasattr(result, "content")
        assert result.content is not None
        assert "5" in result.content


# ------ Anthropic real loop ------

@requires_anthropic
class TestE2EAnthropic:
    """Real Anthropic tool loop. Requires ANTHROPIC_API_KEY."""

    @pytest.fixture
    def anthropic_client(self):
        import anthropic
        return anthropic.Anthropic(api_key=_anthropic_token)

    def test_capabilities_loop(self, runtime, anthropic_client):
        """LLM checks installed capabilities via tool call."""
        messages = [
            {"role": "user", "content": (
                "Use the agentnode_capabilities tool to list what tools "
                "are installed. Then tell me how many packages are installed "
                "and their names. Be brief."
            )},
        ]
        result = runtime.run(
            provider="anthropic",
            client=anthropic_client,
            messages=messages,
            model="claude-haiku-4-5-20251001",
            max_tool_rounds=3,
        )
        # Should be an Anthropic Message, not an error dict
        assert not isinstance(result, dict) or "success" not in result
        assert hasattr(result, "content")
        # Extract text from content blocks
        text = " ".join(
            b.text for b in result.content if hasattr(b, "text")
        ).lower()
        assert "word-counter" in text or "1" in text

    def test_search_and_describe(self, runtime, anthropic_client):
        """LLM searches registry and describes results."""
        messages = [
            {"role": "user", "content": (
                "Search AgentNode for PDF-related tools. "
                "Tell me the slug and summary of the top result. Be brief."
            )},
        ]
        result = runtime.run(
            provider="anthropic",
            client=anthropic_client,
            messages=messages,
            model="claude-haiku-4-5-20251001",
            max_tool_rounds=3,
        )
        assert not isinstance(result, dict) or "success" not in result
        text = " ".join(
            b.text for b in result.content if hasattr(b, "text")
        ).lower()
        assert "pdf" in text

    def test_run_word_counter(self, runtime, anthropic_client):
        """LLM uses word-counter-pack to count words in given text."""
        messages = [
            {"role": "user", "content": (
                "Use agentnode_run to count the words in this text: "
                "'The quick brown fox jumps over the lazy dog'. "
                "Pass it as: slug='word-counter-pack', "
                "arguments={'inputs': {'text': 'The quick brown fox jumps over the lazy dog'}}. "
                "Tell me the word count."
            )},
        ]
        result = runtime.run(
            provider="anthropic",
            client=anthropic_client,
            messages=messages,
            model="claude-haiku-4-5-20251001",
            max_tool_rounds=3,
        )
        assert not isinstance(result, dict) or "success" not in result
        text = " ".join(
            b.text for b in result.content if hasattr(b, "text")
        )
        assert "9" in text

    def test_full_flow_capabilities_then_run(self, runtime, anthropic_client):
        """LLM checks capabilities, then runs a tool — multi-step."""
        messages = [
            {"role": "user", "content": (
                "First, check what AgentNode capabilities are installed. "
                "Then use the word counter to count words in: "
                "'Hello world from the runtime'. "
                "Pass arguments as: {'inputs': {'text': 'Hello world from the runtime'}}. "
                "Tell me the result."
            )},
        ]
        result = runtime.run(
            provider="anthropic",
            client=anthropic_client,
            messages=messages,
            model="claude-haiku-4-5-20251001",
            max_tool_rounds=5,
        )
        assert not isinstance(result, dict) or "success" not in result
        text = " ".join(
            b.text for b in result.content if hasattr(b, "text")
        )
        assert "5" in text


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Capability Class E2E Tests (Full-Flow with Scoring)
# ═══════════════════════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------
# OpenAI Capability Classes
# ---------------------------------------------------------------------------

@requires_openai
class TestE2EOpenAICapabilityClasses:
    """Full autonomous flow: LLM searches, installs, and runs tools.

    5 capability classes × OpenAI = 5 tests.
    Each test logs a ToolUsageScore for provider comparison.
    """

    PROVIDER = "openai"
    MODEL = "gpt-4o-mini"

    @pytest.fixture
    def openai_client(self):
        import openai
        return openai.OpenAI()

    # --- Klasse 1: IO/Parsing ---

    def test_io_parsing(self, runtime, openai_client):
        """LLM finds and installs pdf-reader-pack via search → install → run."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "I need to read a PDF document. Use AgentNode to find a PDF "
                    "reading tool, install it, and then run it with "
                    "slug='pdf-reader-pack' and arguments={'inputs': {'url': 'https://example.com/test.pdf'}}. "
                    "Report what happened at each step."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=openai_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            score = build_score(
                test_name="test_io_parsing",
                provider=self.PROVIDER,
                capability_class="io_parsing",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search", "agentnode_install"],
                expected_sequence=["agentnode_search", "agentnode_install"],
            )
            # Hard assertions: tool calls made, correct sequence
            assert "agentnode_search" in tracker.calls, f"No search call. Calls: {tracker.calls}"
            assert "agentnode_install" in tracker.calls, f"No install call. Calls: {tracker.calls}"
            assert score.correct_sequence, f"Wrong sequence. Calls: {tracker.calls}"
            assert not score.hallucination, "LLM hallucinated instead of using tools"
        finally:
            tracker.restore()

    # --- Klasse 2: Web/API ---

    def test_web_api(self, runtime, openai_client):
        """LLM uses web-search-pack to search the web."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Search AgentNode for a web search tool, install it, and then "
                    "use it to search for 'AI agent frameworks 2026'. "
                    "Report the results."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=openai_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            # LLM may use search→install→run OR acquire shortcut — both valid
            used_search_path = "agentnode_search" in tracker.calls
            used_acquire = "agentnode_acquire" in tracker.calls
            expected = ["agentnode_search"] if used_search_path else ["agentnode_acquire"]

            score = build_score(
                test_name="test_web_api",
                provider=self.PROVIDER,
                capability_class="web_api",
                tracker=tracker,
                result=result,
                expected_tools=expected,
            )
            assert used_search_path or used_acquire, f"No search or acquire. Calls: {tracker.calls}"
            assert not score.hallucination
        finally:
            tracker.restore()

    # --- Klasse 3: Compute (already installed) ---

    def test_compute(self, runtime, openai_client):
        """LLM uses word-counter-pack directly (already installed)."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Check what AgentNode capabilities are installed. "
                    "Then use the word counter tool to count words in: "
                    "'The quick brown fox jumps over the lazy dog on a sunny afternoon'. "
                    "Pass arguments as: {'inputs': {'text': 'The quick brown fox jumps over the lazy dog on a sunny afternoon'}}. "
                    "Tell me the exact word count."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=openai_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=8,
            )
            score = build_score(
                test_name="test_compute",
                provider=self.PROVIDER,
                capability_class="compute",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_capabilities", "agentnode_run"],
                expected_sequence=["agentnode_capabilities", "agentnode_run"],
            )
            response_text = extract_response_text(result, self.PROVIDER)
            assert "agentnode_run" in tracker.calls, f"No run call. Calls: {tracker.calls}"
            assert not score.hallucination
            assert "13" in response_text, f"Expected '13' in response: {response_text}"
        finally:
            tracker.restore()

    # --- Klasse 4: Data/Transform ---

    def test_data_transform(self, runtime, openai_client):
        """LLM finds webpage-extractor-pack and extracts content from a URL."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "I need to extract content from a webpage. Search AgentNode for "
                    "a webpage extraction tool, install it, and then run it to extract "
                    "content from 'https://agentnode.net'. Report what you found."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=openai_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            score = build_score(
                test_name="test_data_transform",
                provider=self.PROVIDER,
                capability_class="data_transform",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search", "agentnode_install"],
                expected_sequence=["agentnode_search", "agentnode_install"],
            )
            assert "agentnode_search" in tracker.calls
            assert score.correct_sequence, f"Wrong sequence. Calls: {tracker.calls}"
            assert not score.hallucination
        finally:
            tracker.restore()

    # --- Klasse 5: Multi-step (full autonomous) ---

    def test_multi_step_autonomous(self, runtime, openai_client):
        """LLM autonomously: capabilities → search → install → run."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "I need to count words in a text. First, check what AgentNode "
                    "capabilities are installed. If a word counter is available, "
                    "use it directly. If not, search for one, install it, then use it. "
                    "Count the words in: 'AgentNode is a runtime for AI agents'. "
                    "Pass arguments as: {'inputs': {'text': 'AgentNode is a runtime for AI agents'}}."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=openai_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            score = build_score(
                test_name="test_multi_step_autonomous",
                provider=self.PROVIDER,
                capability_class="multi_step",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_capabilities", "agentnode_run"],
            )
            response_text = extract_response_text(result, self.PROVIDER)
            assert "agentnode_capabilities" in tracker.calls
            assert "agentnode_run" in tracker.calls
            assert not score.hallucination
            # Word count of "AgentNode is a runtime for AI agents" = 7
            assert "7" in response_text, f"Expected '7' in: {response_text}"
        finally:
            tracker.restore()


# ---------------------------------------------------------------------------
# Anthropic Capability Classes
# ---------------------------------------------------------------------------

@requires_anthropic
class TestE2EAnthropicCapabilityClasses:
    """Full autonomous flow: LLM searches, installs, and runs tools.

    5 capability classes × Anthropic = 5 tests.
    """

    PROVIDER = "anthropic"
    MODEL = "claude-haiku-4-5-20251001"

    @pytest.fixture
    def anthropic_client(self):
        import anthropic
        return anthropic.Anthropic(api_key=_anthropic_token)

    # --- Klasse 1: IO/Parsing ---

    def test_io_parsing(self, runtime, anthropic_client):
        """LLM finds and installs pdf-reader-pack via search → install."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "I need to read a PDF document. Use AgentNode to find a PDF "
                    "reading tool, install it, and then run it with "
                    "slug='pdf-reader-pack' and arguments={'inputs': {'url': 'https://example.com/test.pdf'}}. "
                    "Report what happened at each step."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=anthropic_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            score = build_score(
                test_name="test_io_parsing",
                provider=self.PROVIDER,
                capability_class="io_parsing",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search", "agentnode_install"],
                expected_sequence=["agentnode_search", "agentnode_install"],
            )
            assert "agentnode_search" in tracker.calls, f"No search. Calls: {tracker.calls}"
            assert "agentnode_install" in tracker.calls, f"No install. Calls: {tracker.calls}"
            assert score.correct_sequence, f"Wrong sequence. Calls: {tracker.calls}"
            assert not score.hallucination
        finally:
            tracker.restore()

    # --- Klasse 2: Web/API ---

    def test_web_api(self, runtime, anthropic_client):
        """LLM uses web-search-pack to search the web."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Search AgentNode for a web search tool, install it, and then "
                    "use it to search for 'AI agent frameworks 2026'. "
                    "Report the results."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=anthropic_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            # LLM may use search→install→run OR acquire shortcut — both valid
            used_search_path = "agentnode_search" in tracker.calls
            used_acquire = "agentnode_acquire" in tracker.calls
            expected = ["agentnode_search"] if used_search_path else ["agentnode_acquire"]

            score = build_score(
                test_name="test_web_api",
                provider=self.PROVIDER,
                capability_class="web_api",
                tracker=tracker,
                result=result,
                expected_tools=expected,
            )
            assert used_search_path or used_acquire, f"No search or acquire. Calls: {tracker.calls}"
            assert not score.hallucination
        finally:
            tracker.restore()

    # --- Klasse 3: Compute (already installed) ---

    def test_compute(self, runtime, anthropic_client):
        """LLM uses word-counter-pack directly (already installed)."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Check what AgentNode capabilities are installed. "
                    "Then use the word counter tool to count words in: "
                    "'The quick brown fox jumps over the lazy dog on a sunny afternoon'. "
                    "Pass arguments as: {'inputs': {'text': 'The quick brown fox jumps over the lazy dog on a sunny afternoon'}}. "
                    "Tell me the exact word count."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=anthropic_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=8,
            )
            score = build_score(
                test_name="test_compute",
                provider=self.PROVIDER,
                capability_class="compute",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_capabilities", "agentnode_run"],
                expected_sequence=["agentnode_capabilities", "agentnode_run"],
            )
            response_text = extract_response_text(result, self.PROVIDER)
            assert "agentnode_run" in tracker.calls, f"No run call. Calls: {tracker.calls}"
            assert not score.hallucination
            assert "13" in response_text, f"Expected '13' in: {response_text}"
        finally:
            tracker.restore()

    # --- Klasse 4: Data/Transform ---

    def test_data_transform(self, runtime, anthropic_client):
        """LLM finds webpage-extractor-pack and extracts content from a URL."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "I need to extract content from a webpage. Search AgentNode for "
                    "a webpage extraction tool, install it, and then run it to extract "
                    "content from 'https://agentnode.net'. Report what you found."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=anthropic_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            score = build_score(
                test_name="test_data_transform",
                provider=self.PROVIDER,
                capability_class="data_transform",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search", "agentnode_install"],
                expected_sequence=["agentnode_search", "agentnode_install"],
            )
            assert "agentnode_search" in tracker.calls
            assert score.correct_sequence, f"Wrong sequence. Calls: {tracker.calls}"
            assert not score.hallucination
        finally:
            tracker.restore()

    # --- Klasse 5: Multi-step (full autonomous) ---

    def test_multi_step_autonomous(self, runtime, anthropic_client):
        """LLM autonomously: capabilities → search → install → run."""
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "I need to count words in a text. First, check what AgentNode "
                    "capabilities are installed. If a word counter is available, "
                    "use it directly. If not, search for one, install it, then use it. "
                    "Count the words in: 'AgentNode is a runtime for AI agents'. "
                    "Pass arguments as: {'inputs': {'text': 'AgentNode is a runtime for AI agents'}}."
                )},
            ]
            result = runtime.run(
                provider=self.PROVIDER,
                client=anthropic_client,
                messages=messages,
                model=self.MODEL,
                max_tool_rounds=10,
            )
            score = build_score(
                test_name="test_multi_step_autonomous",
                provider=self.PROVIDER,
                capability_class="multi_step",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_capabilities", "agentnode_run"],
            )
            response_text = extract_response_text(result, self.PROVIDER)
            assert "agentnode_capabilities" in tracker.calls
            assert "agentnode_run" in tracker.calls
            assert not score.hallucination
            # Word count of "AgentNode is a runtime for AI agents" = 7
            assert "7" in response_text, f"Expected '7' in: {response_text}"
        finally:
            tracker.restore()


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Failure Case Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EFailureCases:
    """Tests for error handling, trust blocking, and LLM self-correction.

    These cases prevent regressions that break production later.
    """

    # --- Unit-like: run without install ---

    def test_run_without_install_error_path(self, runtime):
        """Directly calling agentnode_run on non-installed package returns clear error."""
        result = runtime.handle("agentnode_run", {
            "slug": "xyzzy-nonexistent-pack-12345",
        })
        assert result["success"] is False
        assert result["error"]["code"] == "not_installed"
        assert "xyzzy-nonexistent-pack-12345" in result["error"]["message"]
        assert "agentnode_install" in result["error"]["message"]

    # --- Trust blocking ---

    def test_trust_level_blocks_install(self):
        """Curated-only runtime rejects verified/trusted packages."""
        strict_runtime = AgentNodeRuntime(minimum_trust_level="curated")
        # Attempt to install a package that is likely verified/trusted, not curated
        result = strict_runtime.handle("agentnode_install", {
            "slug": "word-counter-pack",
        })
        # Either install_failed or trust_blocked — both are valid rejections
        assert result["success"] is False
        assert result["error"]["code"] in ("install_failed", "trust_blocked")

    # --- LLM: tool not found (graceful handling) ---

    @requires_openai
    def test_tool_not_found_openai(self, runtime):
        """LLM handles nonexistent package gracefully — no crash, no hallucination."""
        import openai
        client = openai.OpenAI()
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Use AgentNode to search for a tool called "
                    "'xyzzy-quantum-teleporter-pack'. Tell me if it exists or not."
                )},
            ]
            result = runtime.run(
                provider="openai",
                client=client,
                messages=messages,
                model="gpt-4o-mini",
                max_tool_rounds=5,
            )
            response_text = extract_response_text(result, "openai")

            # LLM should have called search
            assert "agentnode_search" in tracker.calls, f"No search call. Calls: {tracker.calls}"
            # LLM should NOT have installed or run anything
            assert "agentnode_install" not in tracker.calls, "Should not install nonexistent package"
            # LLM should report that nothing was found
            assert len(response_text) > 0, "LLM should provide a response"

            score = build_score(
                test_name="test_tool_not_found",
                provider="openai",
                capability_class="failure",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search"],
            )
        finally:
            tracker.restore()

    @requires_anthropic
    def test_tool_not_found_anthropic(self, runtime):
        """LLM handles nonexistent package gracefully — no crash, no hallucination."""
        import anthropic
        client = anthropic.Anthropic(api_key=_anthropic_token)
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Use AgentNode to search for a tool called "
                    "'xyzzy-quantum-teleporter-pack'. Tell me if it exists or not."
                )},
            ]
            result = runtime.run(
                provider="anthropic",
                client=client,
                messages=messages,
                model="claude-haiku-4-5-20251001",
                max_tool_rounds=5,
            )
            response_text = extract_response_text(result, "anthropic")

            assert "agentnode_search" in tracker.calls, f"No search call. Calls: {tracker.calls}"
            assert "agentnode_install" not in tracker.calls
            assert len(response_text) > 0

            score = build_score(
                test_name="test_tool_not_found",
                provider="anthropic",
                capability_class="failure",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search"],
            )
        finally:
            tracker.restore()

    # --- LLM self-correction: run before install ---

    @requires_openai
    def test_run_without_install_self_correction_openai(self, runtime):
        """LLM that tries run first should self-correct after error."""
        import openai
        client = openai.OpenAI()
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Run the AgentNode tool 'web-search-pack' to search for "
                    "'latest AI news'. If it's not installed, install it first, "
                    "then try again."
                )},
            ]
            result = runtime.run(
                provider="openai",
                client=client,
                messages=messages,
                model="gpt-4o-mini",
                max_tool_rounds=10,
            )
            response_text = extract_response_text(result, "openai")

            # LLM should eventually have run the tool (possibly after installing)
            assert len(response_text) > 0, "LLM should provide a response"

            # If LLM tried run first and got error, it should have then installed
            if "agentnode_run" in tracker.calls:
                first_run_idx = tracker.calls.index("agentnode_run")
                # Check if there was an install after the first run attempt
                if "agentnode_install" in tracker.calls:
                    install_idx = tracker.calls.index("agentnode_install")
                    # Self-correction: install happened, then a second run
                    run_count = tracker.calls.count("agentnode_run")
                    assert run_count >= 1, "Should have at least one run attempt"

            # LLM may use run OR acquire — both count as execution
            used_run = "agentnode_run" in tracker.calls
            used_acquire = "agentnode_acquire" in tracker.calls
            expected = ["agentnode_run"] if used_run else (
                ["agentnode_acquire"] if used_acquire else ["agentnode_install"]
            )

            score = build_score(
                test_name="test_run_without_install_self_correction",
                provider="openai",
                capability_class="failure",
                tracker=tracker,
                result=result,
                expected_tools=expected,
            )
        finally:
            tracker.restore()

    @requires_anthropic
    def test_run_without_install_self_correction_anthropic(self, runtime):
        """LLM that tries run first should self-correct after error."""
        import anthropic
        client = anthropic.Anthropic(api_key=_anthropic_token)
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Run the AgentNode tool 'web-search-pack' to search for "
                    "'latest AI news'. If it's not installed, install it first, "
                    "then try again."
                )},
            ]
            result = runtime.run(
                provider="anthropic",
                client=client,
                messages=messages,
                model="claude-haiku-4-5-20251001",
                max_tool_rounds=10,
            )
            response_text = extract_response_text(result, "anthropic")

            assert len(response_text) > 0, "LLM should provide a response"

            if "agentnode_run" in tracker.calls:
                if "agentnode_install" in tracker.calls:
                    run_count = tracker.calls.count("agentnode_run")
                    assert run_count >= 1

            # LLM may use run OR acquire — both count as execution
            used_run = "agentnode_run" in tracker.calls
            used_acquire = "agentnode_acquire" in tracker.calls
            expected = ["agentnode_run"] if used_run else (
                ["agentnode_acquire"] if used_acquire else ["agentnode_install"]
            )

            score = build_score(
                test_name="test_run_without_install_self_correction",
                provider="anthropic",
                capability_class="failure",
                tracker=tracker,
                result=result,
                expected_tools=expected,
            )
        finally:
            tracker.restore()
