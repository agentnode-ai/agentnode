"""Endpoint-A — the OpenAI-compatible provider registry."""
from __future__ import annotations

from agentnode_sdk.llm_providers import (
    KNOWN_PROVIDERS,
    known_provider_names,
    resolve_provider_spec,
)


def test_presets_are_complete():
    for name, spec in KNOWN_PROVIDERS.items():
        for key in ("base_url", "api_key_env", "default_model", "requires_key"):
            assert key in spec, f"{name} missing {key}"
        # compat endpoints (base_url set) MUST carry a model — the broker's
        # bare OpenAI default would 404 there
        if spec["base_url"]:
            assert spec["default_model"], f"{name} needs a default model"


def test_expected_preset_names():
    for name in ("openai", "anthropic", "openrouter", "deepseek",
                 "mistral", "qwen", "gemini", "ollama"):
        assert name in KNOWN_PROVIDERS


def test_gemini_uses_googles_openai_compat_endpoint():
    spec = resolve_provider_spec("gemini")
    assert spec["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert spec["base_url"].endswith("/")          # trailing slash required
    assert spec["api_key_env"] == "GEMINI_API_KEY"
    assert spec["default_model"] == "gemini-2.0-flash"
    assert spec["requires_key"] is True


def test_ollama_is_keyless_localhost():
    spec = resolve_provider_spec("ollama")
    assert spec["requires_key"] is False
    assert spec["base_url"] == "http://localhost:11434/v1"
    assert spec["default_model"]            # never empty (broker default 404s)


def test_resolve_preset_without_config():
    spec = resolve_provider_spec("deepseek")
    assert spec["base_url"] == "https://api.deepseek.com/v1"
    assert spec["api_key_env"] == "DEEPSEEK_API_KEY"
    assert spec["default_model"] == "deepseek-chat"


def test_config_override_beats_preset():
    cfg = {"llm": {"providers": {"deepseek": {
        "model": "deepseek-reasoner", "base_url": "https://proxy.local/v1"}}}}
    spec = resolve_provider_spec("deepseek", cfg)
    assert spec["default_model"] == "deepseek-reasoner"   # "model" alias works
    assert spec["base_url"] == "https://proxy.local/v1"
    assert spec["api_key_env"] == "DEEPSEEK_API_KEY"      # preset rest intact


def test_custom_provider_from_config_only():
    cfg = {"llm": {"providers": {"myvllm": {
        "base_url": "http://10.0.0.5:8000/v1", "model": "llama",
        "requires_key": False}}}}
    spec = resolve_provider_spec("myvllm", cfg)
    assert spec["base_url"] == "http://10.0.0.5:8000/v1"
    assert spec["default_model"] == "llama"
    assert spec["requires_key"] is False
    assert spec["api_key_env"] == ""


def test_unknown_provider_is_none():
    assert resolve_provider_spec("nope") is None
    assert resolve_provider_spec("") is None


def test_known_names_include_custom():
    cfg = {"llm": {"providers": {"myvllm": {"base_url": "x", "model": "m"}}}}
    names = known_provider_names(cfg)
    assert names.index("openai") == 0       # presets first, stable order
    assert "myvllm" in names
