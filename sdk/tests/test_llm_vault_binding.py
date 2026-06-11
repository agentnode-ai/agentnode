"""UX-2 — _auto_detect_llm vault integration: env beats vault, vault is the
persistent default, openrouter binding carries the namespaced model.

Provider SDKs are stubbed via sys.modules so the tests run without openai/
anthropic installed and we can assert constructor kwargs (never a real client).
"""
from __future__ import annotations

import sys
import types

import pytest

from agentnode_sdk import credential_store as cs
from agentnode_sdk.runtimes.agent_runner import _auto_detect_llm, _llm_binding_from_providers


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


class _StubClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setitem(cs._keyring_state, "available", None)
    fake = FakeKeyring()  # ONE shared instance — set and get must hit the same store
    monkeypatch.setattr(cs, "_get_keyring_backend", lambda: fake)
    # hermetic: the dev machine's real ~/.agentnode/.env must not leak in
    monkeypatch.setattr(
        "agentnode_sdk.runtimes.agent_runner._load_agentnode_env", lambda: None)
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "DASHSCOPE_API_KEY",
                "GEMINI_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # stub provider SDKs (records constructor kwargs)
    openai_mod = types.ModuleType("openai")
    openai_mod.OpenAI = _StubClient
    anthropic_mod = types.ModuleType("anthropic")
    anthropic_mod.Anthropic = _StubClient
    monkeypatch.setitem(sys.modules, "openai", openai_mod)
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_mod)
    yield


def test_no_keys_anywhere_returns_none():
    assert _auto_detect_llm() is None


def test_vault_openai_binding_when_no_env():
    cs.set_credential("openai", "sk-vault-openai-key1", auth_type="api_key")
    binding = _auto_detect_llm()
    assert binding is not None
    assert binding["provider"] == "openai"
    assert binding["model"] == ""
    assert binding["client"].kwargs == {"api_key": "sk-vault-openai-key1"}


def test_vault_anthropic_binding():
    cs.set_credential("anthropic", "sk-vault-anthropic-1", auth_type="api_key")
    binding = _auto_detect_llm()
    assert binding["provider"] == "anthropic"
    assert binding["client"].kwargs == {"api_key": "sk-vault-anthropic-1"}


def test_vault_openrouter_binding_has_namespaced_model():
    """The broker's bare 'gpt-4o-mini' default 404s on OpenRouter — the vault
    binding must pin the namespaced model and the base_url."""
    cs.set_credential("openrouter", "sk-or-vault-key-12", auth_type="api_key")
    binding = _auto_detect_llm()
    assert binding["provider"] == "openai"        # openai-compatible client
    assert binding["model"] == "openai/gpt-4o-mini"
    assert binding["client"].kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert binding["client"].kwargs["api_key"] == "sk-or-vault-key-12"


def test_env_var_beats_vault(monkeypatch):
    cs.set_credential("anthropic", "sk-vault-anthropic-1", auth_type="api_key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-key-1")
    binding = _auto_detect_llm()
    assert binding["provider"] == "openai"        # env won
    assert binding["client"].kwargs == {"api_key": "sk-env-openai-key-1"}


def test_default_provider_config_orders_vault(monkeypatch):
    cs.set_credential("openai", "sk-vault-openai-key1", auth_type="api_key")
    cs.set_credential("anthropic", "sk-vault-anthropic-1", auth_type="api_key")
    monkeypatch.setattr(
        "agentnode_sdk.config.load_config",
        lambda: {"llm": {"default_provider": "anthropic"}})
    binding = _llm_binding_from_providers()
    assert binding["provider"] == "anthropic"     # config-preferred first


def test_vault_default_order_openai_first():
    cs.set_credential("openai", "sk-vault-openai-key1", auth_type="api_key")
    cs.set_credential("anthropic", "sk-vault-anthropic-1", auth_type="api_key")
    binding = _llm_binding_from_providers()
    assert binding["provider"] == "openai"


# --- Endpoint-A: OpenAI-compatible providers ----------------------------------

def test_deepseek_binding_via_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-env-key-001")
    binding = _auto_detect_llm()
    assert binding["provider"] == "openai"            # openai-compatible shape
    assert binding["model"] == "deepseek-chat"
    assert binding["client"].kwargs["base_url"] == "https://api.deepseek.com/v1"
    assert binding["client"].kwargs["api_key"] == "sk-ds-env-key-001"


def test_deepseek_binding_via_vault():
    cs.set_credential("deepseek", "sk-ds-vault-key-01", auth_type="api_key")
    binding = _auto_detect_llm()
    assert binding["model"] == "deepseek-chat"
    assert binding["client"].kwargs["api_key"] == "sk-ds-vault-key-01"


def test_deepseek_env_beats_vault(monkeypatch):
    cs.set_credential("deepseek", "sk-ds-vault-key-01", auth_type="api_key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-env-key-002")
    binding = _auto_detect_llm()
    assert binding["client"].kwargs["api_key"] == "sk-ds-env-key-002"


def test_mistral_and_qwen_bindings(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral-key-001")
    b = _auto_detect_llm()
    assert b["model"] == "mistral-small-latest"
    assert b["client"].kwargs["base_url"] == "https://api.mistral.ai/v1"
    monkeypatch.delenv("MISTRAL_API_KEY")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-qwen-key-00001")
    b = _auto_detect_llm()
    assert b["model"] == "qwen-plus"
    assert "compatible-mode" in b["client"].kwargs["base_url"]


def test_gemini_binding_via_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-gem-env-key-01")
    binding = _auto_detect_llm()
    assert binding["provider"] == "openai"            # compat shape, broker-ready
    assert binding["model"] == "gemini-2.0-flash"
    assert binding["client"].kwargs["base_url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/")
    assert binding["client"].kwargs["api_key"] == "AIza-gem-env-key-01"


def test_gemini_binding_via_vault():
    cs.set_credential("gemini", "AIza-gem-vault-key1", auth_type="api_key")
    binding = _auto_detect_llm()
    assert binding["model"] == "gemini-2.0-flash"
    assert binding["client"].kwargs["api_key"] == "AIza-gem-vault-key1"


def test_gemini_env_beats_vault(monkeypatch):
    cs.set_credential("gemini", "AIza-gem-vault-key1", auth_type="api_key")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-gem-env-key-02")
    binding = _auto_detect_llm()
    assert binding["client"].kwargs["api_key"] == "AIza-gem-env-key-02"


def test_ollama_keyless_requires_explicit_selection():
    # no keys anywhere, ollama NOT selected -> no surprise binding
    assert _auto_detect_llm() is None


def test_ollama_keyless_via_default_provider(monkeypatch):
    monkeypatch.setattr(
        "agentnode_sdk.config.load_config",
        lambda: {"llm": {"default_provider": "ollama"}})
    binding = _auto_detect_llm()
    assert binding["provider"] == "openai"
    assert binding["model"] == "llama3.2"
    assert binding["client"].kwargs["base_url"] == "http://localhost:11434/v1"
    assert binding["client"].kwargs["api_key"] == "ollama"    # dummy, keyless


def test_ollama_model_override_via_config(monkeypatch):
    monkeypatch.setattr(
        "agentnode_sdk.config.load_config",
        lambda: {"llm": {"default_provider": "ollama",
                          "providers": {"ollama": {"model": "qwen2.5-coder"}}}})
    binding = _auto_detect_llm()
    assert binding["model"] == "qwen2.5-coder"


def test_custom_endpoint_from_config(monkeypatch):
    monkeypatch.setattr(
        "agentnode_sdk.config.load_config",
        lambda: {"llm": {"default_provider": "openai",
                          "providers": {"myvllm": {
                              "base_url": "http://10.0.0.5:8000/v1",
                              "model": "meta-llama/Llama-3.1-8B",
                              "requires_key": False}}}})
    binding = _auto_detect_llm()
    assert binding["client"].kwargs["base_url"] == "http://10.0.0.5:8000/v1"
    assert binding["model"] == "meta-llama/Llama-3.1-8B"


def test_custom_endpoint_without_model_skipped(monkeypatch):
    # never guess a model for a compat endpoint (broker default would 404)
    monkeypatch.setattr(
        "agentnode_sdk.config.load_config",
        lambda: {"llm": {"providers": {"broken": {
            "base_url": "http://10.0.0.5:8000/v1", "requires_key": False}}}})
    assert _auto_detect_llm() is None


def test_openrouter_still_pinned_via_registry():
    cs.set_credential("openrouter", "sk-or-vault-key-12", auth_type="api_key")
    binding = _auto_detect_llm()
    assert binding["model"] == "openai/gpt-4o-mini"
    assert binding["client"].kwargs["base_url"] == "https://openrouter.ai/api/v1"


def test_broker_serves_deepseek_binding(monkeypatch):
    """Sandbox broker inherits new providers with zero broker changes."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-broker-key-1")

    class _Msg:
        content = "deepseek completion"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        @staticmethod
        def create(**kw):
            assert kw["model"] == "deepseek-chat"     # binding model used
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client(_StubClient):
        chat = _Chat()

    sys.modules["openai"].OpenAI = _Client
    from agentnode_sdk.runtimes.agent_llm_broker import host_llm_broker
    out = host_llm_broker([{"role": "user", "content": "hi"}])
    assert out == {"role": "assistant", "content": "deepseek completion"}
    assert "sk-ds-broker-key-1" not in str(out)


def test_no_key_in_logs_for_new_provider(monkeypatch, caplog):
    import logging

    class _Boom(_StubClient):
        def __init__(self, **kwargs):
            raise RuntimeError("ctor fail with sk-ds-LEAK-key-1")

    sys.modules["openai"].OpenAI = _Boom
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-LEAK-key-1")
    with caplog.at_level(logging.DEBUG):
        _auto_detect_llm()
    assert "sk-ds-LEAK-key-1" not in caplog.text          # type-only logging


def test_host_llm_broker_works_with_vault_binding(monkeypatch):
    """Integration: the sandbox broker resolves a vault key without any env
    var — and the binding (with the key) never crosses into the wire shape."""
    cs.set_credential("openai", "sk-vault-broker-key", auth_type="api_key")

    class _Msg:
        content = "vault completion"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        @staticmethod
        def create(**kw):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client(_StubClient):
        chat = _Chat()

    sys.modules["openai"].OpenAI = _Client
    from agentnode_sdk.runtimes.agent_llm_broker import host_llm_broker
    out = host_llm_broker([{"role": "user", "content": "hi"}])
    assert out == {"role": "assistant", "content": "vault completion"}
    assert "sk-vault-broker-key" not in str(out)
