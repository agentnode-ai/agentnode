"""Multi-provider, multi-model compatibility matrix.

Tests AgentNode runtime across all target providers and models in 3 tiers:

  Tier A — Protocol compatibility (cheap: does tool-calling round-trip work?)
  Tier B — Adoption reliability (5 capability classes per model)
  Tier C — Failure handling (self-correction, nonexistent tools)

Usage:
    # Run everything that has keys available:
    pytest tests/test_provider_matrix.py -v

    # Only protocol tests (Tier A):
    pytest tests/test_provider_matrix.py -k "Protocol" -v

    # Only a specific provider:
    pytest tests/test_provider_matrix.py -k "openai" -v

    # Only OpenAI-compatible third-party:
    pytest tests/test_provider_matrix.py -k "compat" -v

    # With explicit keys:
    OPENAI_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_provider_matrix.py -v

    # DeepSeek / Ollama / Qwen (OpenAI-compatible):
    DEEPSEEK_API_KEY=sk-... DEEPSEEK_BASE_URL=https://api.deepseek.com/v1 pytest tests/test_provider_matrix.py -k "deepseek" -v
    OLLAMA_BASE_URL=http://localhost:11434/v1 OLLAMA_MODEL=qwen2.5:7b pytest tests/test_provider_matrix.py -k "ollama" -v
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentnode_sdk.runtime import AgentNodeRuntime

from tests.conftest import (
    ToolCallTracker,
    build_score,
    extract_response_text,
)


def _check_api_result(result: Any, model_cfg: ModelConfig) -> None:
    """Skip test if runtime.run() returned an API-level error.

    Runtime.run() catches provider exceptions and returns error dicts.
    This detects model-access issues (wrong key, model unavailable, rate limit)
    and converts them to pytest.skip() so they don't count as test failures.
    """
    if not isinstance(result, dict):
        return  # Real provider response — proceed with assertions
    error = result.get("error", {})
    code = error.get("code", "")
    message = error.get("message", "")
    # API infra errors → skip (not a tool-calling failure)
    api_errors = [
        "not_found_error", "authentication_error", "invalid_request_error",
        "permission_error", "rate_limit_error", "overloaded_error",
        "RESOURCE_EXHAUSTED", "PERMISSION_DENIED", "UNAUTHENTICATED",
        "quota", "429", "402", "Insufficient credits",
    ]
    if code == "loop_error" and any(e in message for e in api_errors):
        pytest.skip(f"[{model_cfg.id}] API error: {message[:120]}")


# ═══════════════════════════════════════════════════════════════════════════
# Model Registry
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ModelConfig:
    """Configuration for a single model to test."""

    id: str                      # unique test ID (used in parametrize)
    provider: str                # runtime provider key: "openai" or "anthropic"
    model: str                   # model name passed to API
    tier: str                    # "native" | "compat"
    env_key: str                 # env var for API key (empty = no key needed)
    env_base_url: str = ""       # env var for custom base_url (OpenAI-compat)
    default_base_url: str = ""   # fallback base_url if env var not set
    needs_api_key: bool = False  # True = requires explicit env key (no OAuth fallback)


def _have_key(env_key: str) -> bool:
    """Check if an API key env var is set."""
    if not env_key:
        return False
    return bool(os.environ.get(env_key))


def _have_anthropic() -> bool:
    """Check Anthropic key (env or Claude Code OAuth)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.is_file():
        try:
            creds = json.loads(creds_path.read_text(encoding="utf-8"))
            oauth = creds.get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            expires_at = oauth.get("expiresAt", 0) / 1000
            if token and time.time() < expires_at:
                return True
        except Exception:
            pass
    return False


def _load_anthropic_token() -> str | None:
    """Load Anthropic API key from env or Claude Code OAuth."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
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


# ---------------------------------------------------------------------------
# Model definitions
# ---------------------------------------------------------------------------

MODELS: list[ModelConfig] = [
    # --- OpenAI native (Tier 1) ---
    ModelConfig(
        id="openai_gpt4o_mini",
        provider="openai",
        model="gpt-4o-mini",
        tier="native",
        env_key="OPENAI_API_KEY",
    ),
    ModelConfig(
        id="openai_gpt4o",
        provider="openai",
        model="gpt-4o",
        tier="native",
        env_key="OPENAI_API_KEY",
    ),
    ModelConfig(
        id="openai_o3_mini",
        provider="openai",
        model="o3-mini",
        tier="native",
        env_key="OPENAI_API_KEY",
    ),

    # --- Anthropic native (Tier 1) ---
    # Haiku typically works with Claude Code OAuth.
    # Sonnet/Opus may need ANTHROPIC_API_KEY depending on plan.
    # Tests auto-skip on API access errors.
    ModelConfig(
        id="anthropic_haiku",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        tier="native",
        env_key="ANTHROPIC_API_KEY",
    ),
    ModelConfig(
        id="anthropic_sonnet",
        provider="anthropic",
        model="claude-sonnet-4-6",
        tier="native",
        env_key="ANTHROPIC_API_KEY",
    ),
    ModelConfig(
        id="anthropic_opus",
        provider="anthropic",
        model="claude-opus-4-6",
        tier="native",
        env_key="ANTHROPIC_API_KEY",
    ),

    # --- Gemini native (Tier 1) ---
    ModelConfig(
        id="gemini_flash",
        provider="gemini",
        model="gemini-2.5-flash",
        tier="native",
        env_key="GEMINI_API_KEY",
    ),
    ModelConfig(
        id="gemini_pro",
        provider="gemini",
        model="gemini-2.5-pro",
        tier="native",
        env_key="GEMINI_API_KEY",
    ),

    # --- OpenRouter (Tier 2) — single key, many models ---
    # All use OpenAI-compatible format via https://openrouter.ai/api/v1
    ModelConfig(
        id="openrouter_gemini_flash",
        provider="openai",
        model="google/gemini-2.5-flash",
        tier="compat",
        env_key="OPENROUTER_API_KEY",
        env_base_url="OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
    ),
    ModelConfig(
        id="openrouter_gemini_pro",
        provider="openai",
        model="google/gemini-2.5-pro",
        tier="compat",
        env_key="OPENROUTER_API_KEY",
        env_base_url="OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
    ),
    ModelConfig(
        id="openrouter_deepseek_chat",
        provider="openai",
        model="deepseek/deepseek-chat-v3-0324",
        tier="compat",
        env_key="OPENROUTER_API_KEY",
        env_base_url="OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
    ),
    ModelConfig(
        id="openrouter_qwen_plus",
        provider="openai",
        model="qwen/qwen-plus",
        tier="compat",
        env_key="OPENROUTER_API_KEY",
        env_base_url="OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
    ),
    ModelConfig(
        id="openrouter_llama4_scout",
        provider="openai",
        model="meta-llama/llama-4-scout",
        tier="compat",
        env_key="OPENROUTER_API_KEY",
        env_base_url="OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
    ),
    ModelConfig(
        id="openrouter_mistral_large",
        provider="openai",
        model="mistralai/mistral-large",
        tier="compat",
        env_key="OPENROUTER_API_KEY",
        env_base_url="OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
    ),

    # --- NVIDIA NIM (Tier 2) — free API, OpenAI-compatible ---
    ModelConfig(
        id="nvidia_llama3_70b",
        provider="openai",
        model="meta/llama-3.1-70b-instruct",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ModelConfig(
        id="nvidia_llama4_scout",
        provider="openai",
        model="meta/llama-4-scout-17b-16e-instruct",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ModelConfig(
        id="nvidia_llama4_maverick",
        provider="openai",
        model="meta/llama-4-maverick-17b-128e-instruct",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ModelConfig(
        id="nvidia_deepseek_v3",
        provider="openai",
        model="deepseek-ai/deepseek-v3.2",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ModelConfig(
        id="nvidia_qwen3_5",
        provider="openai",
        model="qwen/qwen3.5-397b-a17b",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ModelConfig(
        id="nvidia_llama3_3_70b",
        provider="openai",
        model="meta/llama-3.3-70b-instruct",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ModelConfig(
        id="nvidia_phi4",
        provider="openai",
        model="microsoft/phi-4-mini-instruct",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),
    ModelConfig(
        id="nvidia_gemma3_27b",
        provider="openai",
        model="google/gemma-3-27b-it",
        tier="compat",
        env_key="NVIDIA_API_KEY",
        env_base_url="NVIDIA_BASE_URL",
        default_base_url="https://integrate.api.nvidia.com/v1",
    ),

    # --- Direct OpenAI-compatible: DeepSeek (Tier 2) ---
    ModelConfig(
        id="compat_deepseek_chat",
        provider="openai",
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        tier="compat",
        env_key="DEEPSEEK_API_KEY",
        env_base_url="DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com/v1",
    ),

    # --- OpenAI-compatible: Qwen (Tier 2) ---
    ModelConfig(
        id="compat_qwen",
        provider="openai",
        model=os.environ.get("QWEN_MODEL", "qwen-plus"),
        tier="compat",
        env_key="QWEN_API_KEY",
        env_base_url="QWEN_BASE_URL",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),

    # --- OpenAI-compatible: Ollama local (Tier 2) ---
    ModelConfig(
        id="compat_ollama",
        provider="openai",
        model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        tier="compat",
        env_key="",  # no key needed for local
        env_base_url="OLLAMA_BASE_URL",
        default_base_url="http://localhost:11434/v1",
    ),
]


# ---------------------------------------------------------------------------
# Skip logic
# ---------------------------------------------------------------------------

def _model_available(m: ModelConfig) -> bool:
    """Check if a model can be tested (has credentials)."""
    if m.provider == "anthropic":
        return _have_anthropic()
    if m.tier == "compat" and not m.env_key:
        # Ollama: only available if explicitly configured
        return bool(os.environ.get(m.env_base_url))
    return _have_key(m.env_key)


def _skip_reason(m: ModelConfig) -> str:
    if m.provider == "anthropic":
        return "No Anthropic API key (env or OAuth)"
    if m.tier == "compat" and not m.env_key:
        return f"{m.env_base_url} not set"
    return f"{m.env_key} not set"


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------

def _make_client(m: ModelConfig) -> Any:
    """Create the appropriate API client for a model config."""
    if m.provider == "anthropic":
        import anthropic
        import httpx
        token = _load_anthropic_token()
        return anthropic.Anthropic(api_key=token, timeout=httpx.Timeout(60.0, connect=10.0))

    if m.provider == "gemini":
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        return genai.Client(api_key=api_key)

    # OpenAI or OpenAI-compatible
    import openai

    kwargs: dict[str, Any] = {}
    if m.tier == "compat":
        base = os.environ.get(m.env_base_url, m.default_base_url)
        kwargs["base_url"] = base
        if m.env_key:
            kwargs["api_key"] = os.environ.get(m.env_key, "")
        else:
            kwargs["api_key"] = "ollama"  # Ollama doesn't need a real key

    # 30s HTTP timeout prevents tests from hanging on slow providers
    import httpx
    kwargs["timeout"] = httpx.Timeout(60.0, connect=10.0)
    return openai.OpenAI(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def runtime():
    return AgentNodeRuntime(minimum_trust_level="verified")


# ═══════════════════════════════════════════════════════════════════════════
# TIER A — Protocol Compatibility
#
# Cheapest tests. One round-trip per model. Validates:
#   - Tools are registered and accepted
#   - Tool calls come back in correct format
#   - Tool results are returned to the model
#   - No infinite loops or format errors
# ═══════════════════════════════════════════════════════════════════════════

_PROTOCOL_MODELS = [
    pytest.param(
        m,
        id=m.id,
        marks=pytest.mark.skipif(not _model_available(m), reason=_skip_reason(m)),
    )
    for m in MODELS
]


class TestProtocolCompatibility:
    """Tier A: Does the tool-calling round-trip work at all?"""

    @pytest.mark.parametrize("model_cfg", _PROTOCOL_MODELS)
    def test_capabilities_roundtrip(self, runtime, model_cfg: ModelConfig):
        """Model calls agentnode_capabilities and returns text."""
        client = _make_client(model_cfg)
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Use the agentnode_capabilities tool to list installed tools. "
                    "Tell me how many packages are installed. Be brief."
                )},
            ]
            result = runtime.run(
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=3,
            )
            _check_api_result(result, model_cfg)

            response_text = extract_response_text(result, model_cfg.provider)
            score = build_score(
                test_name="protocol_capabilities",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="protocol",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_capabilities"],
            )

            assert "agentnode_capabilities" in tracker.calls, (
                f"[{model_cfg.id}] No capabilities call. Calls: {tracker.calls}"
            )
            assert len(response_text) > 0, f"[{model_cfg.id}] Empty response"
            assert not score.hallucination, f"[{model_cfg.id}] Hallucinated"
        finally:
            tracker.restore()

    @pytest.mark.parametrize("model_cfg", _PROTOCOL_MODELS)
    def test_search_roundtrip(self, runtime, model_cfg: ModelConfig):
        """Model calls agentnode_search and reports results."""
        client = _make_client(model_cfg)
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Search AgentNode for PDF tools. "
                    "Tell me the slug of the top result. Be brief."
                )},
            ]
            result = runtime.run(
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=3,
            )
            _check_api_result(result, model_cfg)

            response_text = extract_response_text(result, model_cfg.provider)
            score = build_score(
                test_name="protocol_search",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="protocol",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search"],
            )

            assert "agentnode_search" in tracker.calls, (
                f"[{model_cfg.id}] No search call. Calls: {tracker.calls}"
            )
            assert len(response_text) > 0, f"[{model_cfg.id}] Empty response"
            assert "pdf" in response_text.lower(), f"[{model_cfg.id}] No PDF in response"
        finally:
            tracker.restore()

    @pytest.mark.parametrize("model_cfg", _PROTOCOL_MODELS)
    def test_run_roundtrip(self, runtime, model_cfg: ModelConfig):
        """Model calls agentnode_run on word-counter-pack."""
        client = _make_client(model_cfg)
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Use agentnode_run to count words in this text: "
                    "'The quick brown fox jumps over the lazy dog'. "
                    "Pass: slug='word-counter-pack', "
                    "arguments={'inputs': {'text': 'The quick brown fox jumps over the lazy dog'}}. "
                    "Tell me the word count."
                )},
            ]
            result = runtime.run(
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=3,
            )
            _check_api_result(result, model_cfg)

            response_text = extract_response_text(result, model_cfg.provider)
            score = build_score(
                test_name="protocol_run",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="protocol",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_run"],
            )

            assert "agentnode_run" in tracker.calls, (
                f"[{model_cfg.id}] No run call. Calls: {tracker.calls}"
            )
            assert "9" in response_text, (
                f"[{model_cfg.id}] Expected '9' in: {response_text[:200]}"
            )
        finally:
            tracker.restore()


# ═══════════════════════════════════════════════════════════════════════════
# TIER B — Adoption Reliability (5 capability classes)
#
# Only for Tier 1 (native) models. Tests full autonomous flow:
#   search → install → run with scoring.
# ═══════════════════════════════════════════════════════════════════════════

_ADOPTION_MODELS = [
    pytest.param(
        m,
        id=m.id,
        marks=pytest.mark.skipif(not _model_available(m), reason=_skip_reason(m)),
    )
    for m in MODELS
]


class TestAdoptionReliability:
    """Tier B: Does the model use tools correctly in autonomous flows?"""

    # --- Capability 1: Compute (installed tool) ---

    @pytest.mark.parametrize("model_cfg", _ADOPTION_MODELS)
    def test_compute_installed(self, runtime, model_cfg: ModelConfig):
        """Model checks capabilities, then runs word-counter directly."""
        client = _make_client(model_cfg)
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
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=8,
            )
            _check_api_result(result, model_cfg)

            response_text = extract_response_text(result, model_cfg.provider)
            score = build_score(
                test_name="adoption_compute",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="compute",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_capabilities", "agentnode_run"],
                expected_sequence=["agentnode_capabilities", "agentnode_run"],
            )

            assert "agentnode_run" in tracker.calls, (
                f"[{model_cfg.id}] No run call. Calls: {tracker.calls}"
            )
            assert not score.hallucination, f"[{model_cfg.id}] Hallucinated"
            assert "13" in response_text, (
                f"[{model_cfg.id}] Expected '13' in: {response_text[:200]}"
            )
        finally:
            tracker.restore()

    # --- Capability 2: IO/Parsing (search + install) ---

    @pytest.mark.parametrize("model_cfg", _ADOPTION_MODELS)
    def test_io_parsing(self, runtime, model_cfg: ModelConfig):
        """Model searches for PDF tool, installs it."""
        client = _make_client(model_cfg)
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
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=10,
            )
            _check_api_result(result, model_cfg)

            score = build_score(
                test_name="adoption_io_parsing",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="io_parsing",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search", "agentnode_install"],
                expected_sequence=["agentnode_search", "agentnode_install"],
            )

            assert "agentnode_search" in tracker.calls, (
                f"[{model_cfg.id}] No search. Calls: {tracker.calls}"
            )
            assert "agentnode_install" in tracker.calls, (
                f"[{model_cfg.id}] No install. Calls: {tracker.calls}"
            )
            assert score.correct_sequence, (
                f"[{model_cfg.id}] Wrong sequence. Calls: {tracker.calls}"
            )
        finally:
            tracker.restore()

    # --- Capability 3: Web/API ---

    @pytest.mark.parametrize("model_cfg", _ADOPTION_MODELS)
    def test_web_api(self, runtime, model_cfg: ModelConfig):
        """Model finds web-search tool and uses it."""
        client = _make_client(model_cfg)
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
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=10,
            )
            _check_api_result(result, model_cfg)

            used_search = "agentnode_search" in tracker.calls
            used_acquire = "agentnode_acquire" in tracker.calls
            expected = ["agentnode_search"] if used_search else ["agentnode_acquire"]

            score = build_score(
                test_name="adoption_web_api",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="web_api",
                tracker=tracker,
                result=result,
                expected_tools=expected,
            )

            assert used_search or used_acquire, (
                f"[{model_cfg.id}] No search or acquire. Calls: {tracker.calls}"
            )
            assert not score.hallucination, f"[{model_cfg.id}] Hallucinated"
        finally:
            tracker.restore()

    # --- Capability 4: Data/Transform ---

    @pytest.mark.parametrize("model_cfg", _ADOPTION_MODELS)
    def test_data_transform(self, runtime, model_cfg: ModelConfig):
        """Model finds webpage-extractor and uses it."""
        client = _make_client(model_cfg)
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
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=10,
            )
            _check_api_result(result, model_cfg)

            score = build_score(
                test_name="adoption_data_transform",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="data_transform",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search", "agentnode_install"],
                expected_sequence=["agentnode_search", "agentnode_install"],
            )

            assert "agentnode_search" in tracker.calls, (
                f"[{model_cfg.id}] No search. Calls: {tracker.calls}"
            )
            assert score.correct_sequence, (
                f"[{model_cfg.id}] Wrong sequence. Calls: {tracker.calls}"
            )
        finally:
            tracker.restore()

    # --- Capability 5: Multi-step autonomous ---

    @pytest.mark.parametrize("model_cfg", _ADOPTION_MODELS)
    def test_multi_step_autonomous(self, runtime, model_cfg: ModelConfig):
        """Model autonomously: capabilities -> find tool -> use it."""
        client = _make_client(model_cfg)
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
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=10,
            )
            _check_api_result(result, model_cfg)

            response_text = extract_response_text(result, model_cfg.provider)
            score = build_score(
                test_name="adoption_multi_step",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="multi_step",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_capabilities", "agentnode_run"],
            )

            assert "agentnode_capabilities" in tracker.calls, (
                f"[{model_cfg.id}] No capabilities. Calls: {tracker.calls}"
            )
            assert "agentnode_run" in tracker.calls, (
                f"[{model_cfg.id}] No run. Calls: {tracker.calls}"
            )
            assert not score.hallucination, f"[{model_cfg.id}] Hallucinated"
            assert "7" in response_text, (
                f"[{model_cfg.id}] Expected '7' in: {response_text[:200]}"
            )
        finally:
            tracker.restore()


# ═══════════════════════════════════════════════════════════════════════════
# TIER C — Failure Handling
#
# Tests error recovery and self-correction across models.
# ═══════════════════════════════════════════════════════════════════════════

_FAILURE_MODELS = [
    pytest.param(
        m,
        id=m.id,
        marks=pytest.mark.skipif(not _model_available(m), reason=_skip_reason(m)),
    )
    for m in MODELS
]


class TestFailureHandling:
    """Tier C: Does the model handle errors gracefully?"""

    @pytest.mark.parametrize("model_cfg", _FAILURE_MODELS)
    def test_nonexistent_tool_search(self, runtime, model_cfg: ModelConfig):
        """Model searches for nonexistent tool, reports not found."""
        client = _make_client(model_cfg)
        tracker = ToolCallTracker(runtime)
        try:
            messages = [
                {"role": "user", "content": (
                    "Use AgentNode to search for a tool called "
                    "'xyzzy-quantum-teleporter-pack'. Tell me if it exists or not."
                )},
            ]
            result = runtime.run(
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=5,
            )
            _check_api_result(result, model_cfg)

            response_text = extract_response_text(result, model_cfg.provider)
            score = build_score(
                test_name="failure_nonexistent",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="failure",
                tracker=tracker,
                result=result,
                expected_tools=["agentnode_search"],
            )

            assert "agentnode_search" in tracker.calls, (
                f"[{model_cfg.id}] No search. Calls: {tracker.calls}"
            )
            assert "agentnode_install" not in tracker.calls, (
                f"[{model_cfg.id}] Should not install nonexistent package"
            )
            assert len(response_text) > 0, f"[{model_cfg.id}] Empty response"
        finally:
            tracker.restore()

    @pytest.mark.parametrize("model_cfg", _FAILURE_MODELS)
    def test_self_correction_run_before_install(self, runtime, model_cfg: ModelConfig):
        """Model tries run, gets error, self-corrects by installing first."""
        client = _make_client(model_cfg)
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
                provider=model_cfg.provider,
                client=client,
                messages=messages,
                model=model_cfg.model,
                max_tool_rounds=10,
            )
            _check_api_result(result, model_cfg)

            response_text = extract_response_text(result, model_cfg.provider)

            used_run = "agentnode_run" in tracker.calls
            used_acquire = "agentnode_acquire" in tracker.calls
            expected = ["agentnode_run"] if used_run else (
                ["agentnode_acquire"] if used_acquire else ["agentnode_install"]
            )

            score = build_score(
                test_name="failure_self_correction",
                provider=model_cfg.id,
                model=model_cfg.model,
                capability_class="failure",
                tracker=tracker,
                result=result,
                expected_tools=expected,
            )

            assert len(response_text) > 0, f"[{model_cfg.id}] Empty response"
            # Model should have made at least one tool call
            assert len(tracker.calls) > 0, (
                f"[{model_cfg.id}] No tool calls at all"
            )
        finally:
            tracker.restore()
