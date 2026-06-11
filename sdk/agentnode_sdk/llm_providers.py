"""OpenAI-compatible LLM provider registry (Endpoint-A).

One shared source of truth for the LLM endpoints AgentNode can bind: built-in
presets (OpenRouter, DeepSeek, Mistral, Qwen, Ollama — plus the official
OpenAI/Anthropic entries) and user-defined entries/overrides from
``llm.providers.<name>`` in ~/.agentnode/config.json.

The runtime (``_auto_detect_llm``) builds standard OpenAI-compatible bindings
from these specs, so the host LLM broker — and therefore sandboxed agents —
inherit every provider automatically, with the key staying host-side and no
broker/sandbox change.

Compat presets MUST carry a default model: the broker's bare OpenAI default
("gpt-4o-mini") would 404 on most compatible endpoints. ``requires_key:
False`` providers (Ollama) are bound only on explicit selection — never by
probing.
"""
from __future__ import annotations

from typing import Any

# Built-in presets. base_url=None means the provider SDK's official endpoint.
# default_model="" lets the broker pick its own official default.
KNOWN_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "",
        "requires_key": True,
    },
    "anthropic": {
        "base_url": None,
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "",
        "requires_key": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
        "requires_key": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "requires_key": True,
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "default_model": "mistral-small-latest",
        "requires_key": True,
    },
    "qwen": {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-plus",
        "requires_key": True,
    },
    "gemini": {
        # Google's own OpenAI-compatible endpoint (trailing slash required).
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "default_model": "gemini-2.0-flash",
        "requires_key": True,
    },
    "ollama": {
        # key-free local inference: the first agent path with no account and
        # no per-token cost. Bound ONLY when explicitly selected (no probing).
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",
        "default_model": "llama3.2",
        "requires_key": False,
    },
}

_SPEC_KEYS = ("base_url", "api_key_env", "default_model", "requires_key")


def _config_providers(host_config: dict | None) -> dict:
    llm = (host_config or {}).get("llm") or {}
    providers = llm.get("providers")
    return providers if isinstance(providers, dict) else {}


def resolve_provider_spec(name: str, host_config: dict | None = None) -> dict | None:
    """Effective spec for ``name`` = preset merged with the config override
    (``llm.providers.<name>``), or None for an unknown name without a config
    entry. ``model`` is accepted as a friendlier config alias for
    ``default_model``."""
    name = (name or "").lower().strip()
    preset = KNOWN_PROVIDERS.get(name)
    override = _config_providers(host_config).get(name)
    if preset is None and not isinstance(override, dict):
        return None

    spec: dict[str, Any] = dict(preset) if preset else {
        "base_url": None, "api_key_env": "", "default_model": "", "requires_key": True,
    }
    if isinstance(override, dict):
        for k in _SPEC_KEYS:
            if k in override:
                spec[k] = override[k]
        if "model" in override:
            spec["default_model"] = override["model"]
    return spec


def known_provider_names(host_config: dict | None = None) -> list[str]:
    """Preset names first (stable order), then custom config-defined names."""
    names = list(KNOWN_PROVIDERS)
    for n in _config_providers(host_config):
        if n not in names:
            names.append(n)
    return names
