"""B2b-1 — host-side LLM broker. Docker-free; the provider client is mocked.

Proves: key stays host-side (never in the return value), structured completion,
generic leak-free errors, optional-SDK handling, and that the broker is a pure
relay (no eval/exec of agent input).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnode_sdk.runtimes.agent_llm_broker import LlmBrokerError, host_llm_broker

_ADL = "agentnode_sdk.runtimes.agent_runner._auto_detect_llm"


def test_no_provider_clean_error(monkeypatch):
    monkeypatch.setattr(_ADL, lambda *a, **k: None)
    with pytest.raises(LlmBrokerError) as ei:
        host_llm_broker([{"role": "user", "content": "hi"}])
    assert "provider" in str(ei.value).lower()


def test_openai_success_mocked(monkeypatch):
    class _Msg:
        content = "hello from openai"

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

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(_ADL, lambda *a, **k: {"client": _Client(), "provider": "openai", "model": "x"})
    out = host_llm_broker([{"role": "user", "content": "hi"}])
    assert out == {"role": "assistant", "content": "hello from openai"}


def test_anthropic_success_mocked(monkeypatch):
    class _Block:
        text = "hi from anthropic"

    class _Resp:
        content = [_Block()]

    class _Messages:
        @staticmethod
        def create(**kw):
            return _Resp()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(_ADL, lambda *a, **k: {"client": _Client(), "provider": "anthropic", "model": "m"})
    out = host_llm_broker([{"role": "user", "content": "hi"}])
    assert out["role"] == "assistant"
    assert out["content"] == "hi from anthropic"


def test_provider_exception_generic_no_leak(monkeypatch):
    secret = "sk-secret-key-DO-NOT-LEAK"

    class _Completions:
        @staticmethod
        def create(**kw):
            raise RuntimeError(f"401 unauthorized key={secret} url=https://internal.api/v1")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(_ADL, lambda *a, **k: {"client": _Client(), "provider": "openai", "model": "x"})
    with pytest.raises(LlmBrokerError) as ei:
        host_llm_broker([{"role": "user", "content": "hi"}])
    msg = str(ei.value)
    assert secret not in msg          # no API key
    assert "internal" not in msg       # no internal URL
    assert "401" not in msg            # no raw provider text


def test_unsupported_provider(monkeypatch):
    monkeypatch.setattr(_ADL, lambda *a, **k: {"client": object(), "provider": "cohere", "model": "x"})
    with pytest.raises(LlmBrokerError):
        host_llm_broker([])


def test_broker_is_pure_relay_no_dynamic_exec():
    import agentnode_sdk
    src = (Path(agentnode_sdk.__file__).parent / "runtimes" / "agent_llm_broker.py").read_text(encoding="utf-8")
    assert "eval(" not in src
    assert "exec(" not in src
    # the broker returns only role+content — no client, no key fields
    assert "api_key" not in src.lower()
