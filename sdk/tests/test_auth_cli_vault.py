"""UX-2 — auth CLI: set/--from-env/test/status with masked, leak-free output.

Provider HTTP calls are respx-mocked (no real provider, no cost). The keyring
is faked via the credential_store seam.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from agentnode_sdk import credential_store as cs
from agentnode_sdk.cli.auth import (
    cmd_auth_list,
    cmd_auth_set,
    cmd_auth_status,
    cmd_auth_test,
)

SECRET = "sk-FULL-SECRET-VALUE-abcd"


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setitem(cs._keyring_state, "available", None)
    # the CLI consults env (incl. ~/.agentnode/.env) for effective source —
    # keep the test hermetic
    monkeypatch.setattr(
        "agentnode_sdk.runtimes.agent_runner._load_agentnode_env", lambda: None)
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "DASHSCOPE_API_KEY",
                "GEMINI_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield


def _use_keyring(monkeypatch, fake):
    monkeypatch.setattr(cs, "_get_keyring_backend", lambda: fake)


# --- auth set ----------------------------------------------------------------

def test_set_via_getpass_masked_output(monkeypatch, capsys):
    _use_keyring(monkeypatch, FakeKeyring())
    monkeypatch.setattr("getpass.getpass", lambda prompt: SECRET)
    assert cmd_auth_set("openai") == 0
    out = capsys.readouterr().out
    assert SECRET not in out                      # never the full key
    assert "...abcd" in out                       # masked tail only
    assert "OS keychain" in out                   # honest storage label


def test_set_from_env_flag(monkeypatch, capsys):
    _use_keyring(monkeypatch, FakeKeyring())
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    assert cmd_auth_set("openai", from_env="OPENAI_API_KEY") == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "...abcd" in out
    assert cs.get_llm_api_key("openai") == SECRET


def test_set_from_env_missing_var(monkeypatch, capsys):
    _use_keyring(monkeypatch, FakeKeyring())
    assert cmd_auth_set("openai", from_env="NOPE_NOT_SET") == 1


def test_set_fallback_label_is_honest(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)               # no keychain
    monkeypatch.setattr("getpass.getpass", lambda prompt: SECRET)
    assert cmd_auth_set("openai") == 0
    out = capsys.readouterr().out
    assert "plaintext file" in out
    assert "encrypted" not in out.lower()


# --- auth test (respx, free endpoints, no cost) --------------------------------

@respx.mock
def test_auth_test_openai_valid(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    cs.set_credential("openai", SECRET, auth_type="api_key")
    route = respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    assert cmd_auth_test("openai") == 0
    assert route.called
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "valid" in out


@respx.mock
def test_auth_test_invalid_key_exit_1_no_body_echo(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    cs.set_credential("openai", SECRET, auth_type="api_key")
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(
            401, json={"error": {"message": f"Incorrect API key provided: {SECRET}"}}))
    assert cmd_auth_test("openai") == 1
    out = capsys.readouterr().out
    assert SECRET not in out                      # provider body NEVER echoed
    assert "rejected" in out


@respx.mock
def test_auth_test_anthropic_sends_version_header(monkeypatch):
    _use_keyring(monkeypatch, None)
    cs.set_credential("anthropic", SECRET, auth_type="api_key")
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    assert cmd_auth_test("anthropic") == 0
    req = route.calls.last.request
    assert req.headers["x-api-key"] == SECRET
    assert req.headers["anthropic-version"]       # required, else 400 != bad key


@respx.mock
def test_auth_test_openrouter_uses_auth_key_endpoint(monkeypatch):
    # /models is unauthenticated on OpenRouter — /auth/key actually validates
    _use_keyring(monkeypatch, None)
    cs.set_credential("openrouter", SECRET, auth_type="api_key")
    route = respx.get("https://openrouter.ai/api/v1/auth/key").mock(
        return_value=httpx.Response(200, json={"data": {}}))
    assert cmd_auth_test("openrouter") == 0
    assert route.called


def test_auth_test_not_configured_exit_2(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    assert cmd_auth_test("openai") == 2


def test_auth_test_unsupported_provider_exit_2(monkeypatch):
    assert cmd_auth_test("github") == 2


@respx.mock
def test_auth_test_network_error_exit_3_not_invalid(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    cs.set_credential("openai", SECRET, auth_type="api_key")
    respx.get("https://api.openai.com/v1/models").mock(
        side_effect=httpx.ConnectTimeout("timeout"))
    assert cmd_auth_test("openai") == 3
    out = capsys.readouterr().out
    assert "unknown" in out                       # never "rejected" on network errors


@respx.mock
def test_auth_test_5xx_exit_3(monkeypatch):
    _use_keyring(monkeypatch, None)
    cs.set_credential("openai", SECRET, auth_type="api_key")
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(503))
    assert cmd_auth_test("openai") == 3


@respx.mock
def test_auth_test_env_overrides_vault(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    cs.set_credential("openai", "sk-stored-key-0000", auth_type="api_key")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    route = respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    assert cmd_auth_test("openai") == 0
    # the ENV key was tested, and the shadowing is called out
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {SECRET}"
    out = capsys.readouterr().out
    assert "overrides stored credential" in out


# --- auth status / list --------------------------------------------------------

def test_status_shows_effective_source_no_leak(monkeypatch, capsys):
    fake = FakeKeyring()
    _use_keyring(monkeypatch, fake)
    cs.set_credential("openai", SECRET, auth_type="api_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key-9999")
    assert cmd_auth_status() == 0
    out = capsys.readouterr().out
    assert "OS keychain" in out                   # stored credential source
    assert "env var ANTHROPIC_API_KEY" in out     # env source
    assert "not configured" in out                # openrouter
    assert SECRET not in out and "sk-env-key-9999" not in out


def test_list_shows_storage_column_no_leak(monkeypatch, capsys):
    _use_keyring(monkeypatch, FakeKeyring())
    cs.set_credential("openai", SECRET, auth_type="api_key")
    assert cmd_auth_list() == 0
    out = capsys.readouterr().out
    assert "OS keychain" in out
    assert SECRET not in out


# --- Endpoint-B: registry-driven status/test ---------------------------------

def test_status_shows_all_registry_providers(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    assert cmd_auth_status() == 0
    out = capsys.readouterr().out
    for provider in ("openai", "anthropic", "openrouter", "deepseek",
                     "mistral", "qwen", "gemini"):
        assert provider in out
    assert "ollama" in out
    assert "local (keyless)" in out                   # never shown as "missing"
    assert "not selected" in out


def test_status_shows_ollama_selected(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.config.load_config",
                        lambda: {"llm": {"default_provider": "ollama"}})
    assert cmd_auth_status() == 0
    out = capsys.readouterr().out
    assert "selected via llm.default_provider" in out


def test_status_shows_custom_provider(monkeypatch, capsys):
    monkeypatch.setattr(
        "agentnode_sdk.config.load_config",
        lambda: {"llm": {"providers": {"myvllm": {
            "base_url": "http://10.0.0.5:8000/v1", "model": "llama"}}}})
    assert cmd_auth_status() == 0
    assert "myvllm" in capsys.readouterr().out


@respx.mock
def test_auth_test_deepseek_generic_models_probe(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    cs.set_credential("deepseek", SECRET, auth_type="api_key")
    route = respx.get("https://api.deepseek.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    assert cmd_auth_test("deepseek") == 0
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {SECRET}"
    out = capsys.readouterr().out
    assert SECRET not in out and "valid" in out


@respx.mock
def test_auth_test_deepseek_rejected_no_body_echo(monkeypatch, capsys):
    _use_keyring(monkeypatch, None)
    cs.set_credential("deepseek", SECRET, auth_type="api_key")
    respx.get("https://api.deepseek.com/v1/models").mock(
        return_value=httpx.Response(401, json={"error": f"bad key {SECRET}"}))
    assert cmd_auth_test("deepseek") == 1
    out = capsys.readouterr().out
    assert SECRET not in out                          # body never echoed


@respx.mock
def test_auth_test_gemini_uses_compat_endpoint(monkeypatch):
    _use_keyring(monkeypatch, None)
    cs.set_credential("gemini", SECRET, auth_type="api_key")
    route = respx.get(
        "https://generativelanguage.googleapis.com/v1beta/openai/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    assert cmd_auth_test("gemini") == 0
    assert route.called


@respx.mock
def test_auth_test_openrouter_still_auth_key_endpoint(monkeypatch):
    # /models is unauthenticated on OpenRouter — the exception must survive
    _use_keyring(monkeypatch, None)
    cs.set_credential("openrouter", SECRET, auth_type="api_key")
    route = respx.get("https://openrouter.ai/api/v1/auth/key").mock(
        return_value=httpx.Response(200, json={"data": {}}))
    assert cmd_auth_test("openrouter") == 0
    assert route.called


@respx.mock
def test_auth_test_ollama_reachable_exit_0(monkeypatch, capsys):
    route = respx.get("http://localhost:11434/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    assert cmd_auth_test("ollama") == 0               # keyless: no exit-2 gate
    assert route.called
    out = capsys.readouterr().out
    assert "reachable" in out
    assert "authorization" not in route.calls.last.request.headers  # keyless probe


@respx.mock
def test_auth_test_ollama_unreachable_exit_3_never_rejected(monkeypatch, capsys):
    respx.get("http://localhost:11434/v1/models").mock(
        side_effect=httpx.ConnectError("refused"))
    assert cmd_auth_test("ollama") == 3               # unreachable ≠ rejected
    out = capsys.readouterr().out
    assert "running" in out                            # "is ... Ollama running?"


@respx.mock
def test_auth_test_custom_provider_via_config(monkeypatch):
    _use_keyring(monkeypatch, None)
    monkeypatch.setattr(
        "agentnode_sdk.config.load_config",
        lambda: {"llm": {"providers": {"myvllm": {
            "base_url": "http://10.0.0.5:8000/v1", "model": "llama",
            "requires_key": False}}}})
    route = respx.get("http://10.0.0.5:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}))
    assert cmd_auth_test("myvllm") == 0
    assert route.called


def test_auth_test_unknown_provider_exit_2_b(monkeypatch):
    assert cmd_auth_test("skynet") == 2


def test_registry_is_single_source_of_truth():
    """auth CLI and wizard must not hardcode provider env vars — those come
    from the registry only."""
    import agentnode_sdk
    from pathlib import Path
    for mod in ("cli/auth.py", "cli/setup_wizard.py"):
        src = (Path(agentnode_sdk.__file__).parent / mod).read_text(encoding="utf-8")
        for literal in ("DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "DASHSCOPE_API_KEY",
                        "GEMINI_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY"):
            assert literal not in src, f"{literal} hardcoded in {mod}"


def test_no_secret_in_logs(monkeypatch, caplog):
    """No log record in the store paths ever carries the token."""
    class WriteFails(FakeKeyring):
        def set_password(self, *a):
            raise RuntimeError(f"boom {SECRET}")  # hostile backend error

    _use_keyring(monkeypatch, WriteFails())
    import logging
    with caplog.at_level(logging.DEBUG):
        cs.set_credential("openai", SECRET, auth_type="api_key")
        cs.get_credential("openai")
    assert SECRET not in caplog.text
