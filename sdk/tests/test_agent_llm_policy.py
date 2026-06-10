"""C1 — host-credential LLM broker policy. Docker-free; base broker is a fake.

Proves: default-DENY (no llm_access / enabled:false → refused), opt-in allow within
caps, per-run max_calls, max_input_chars, max_output_chars, host-config ceiling
beats the manifest, and that provider/host errors come back as sanitized per-call
errors (no key / no prompt / no raw provider internals).
"""
from __future__ import annotations

from agentnode_sdk.runtimes.agent_llm_broker import LlmBrokerError
from agentnode_sdk.runtimes.agent_llm_policy import (
    LlmAccessPolicy,
    make_policy_broker,
    resolve_llm_policy,
)

_OK = {"role": "assistant", "content": "ok"}


def _enabled(**caps):
    cfg = {"llm_access": {"enabled": True}}
    cfg["llm_access"].update(caps)
    return cfg


# --- default-deny ----------------------------------------------------------

def test_missing_llm_access_denied():
    pol = resolve_llm_policy({}, {})
    assert pol.enabled is False
    out = make_policy_broker(pol, lambda m: _OK)([{"role": "user", "content": "hi"}])
    assert out["ok"] is False
    assert "not granted" in out["error"].lower()


def test_enabled_false_denied():
    pol = resolve_llm_policy({"llm_access": {"enabled": False}}, {})
    assert pol.enabled is False
    assert make_policy_broker(pol, lambda m: _OK)([])["ok"] is False


def test_enabled_not_boolean_true_denied():
    # enabled must be exactly True, not truthy strings
    assert resolve_llm_policy({"llm_access": {"enabled": "yes"}}, {}).enabled is False


# --- allow within caps -----------------------------------------------------

def test_enabled_true_allowed():
    pol = resolve_llm_policy(_enabled(), {})
    assert pol.enabled is True
    out = make_policy_broker(pol, lambda m: _OK)([{"role": "user", "content": "hi"}])
    assert out == {"ok": True, "completion": _OK}


def test_max_calls_enforced():
    pol = resolve_llm_policy(_enabled(max_calls=2), {})
    b = make_policy_broker(pol, lambda m: _OK)
    assert b([])["ok"] is True
    assert b([])["ok"] is True
    over = b([])
    assert over["ok"] is False and "limit" in over["error"].lower()


def test_max_input_chars_enforced():
    pol = resolve_llm_policy(_enabled(max_input_chars=10), {})
    b = make_policy_broker(pol, lambda m: _OK)
    assert b([{"role": "user", "content": "short"}])["ok"] is True
    big = b([{"role": "user", "content": "x" * 50}])
    assert big["ok"] is False and "input size" in big["error"].lower()


def test_max_output_chars_enforced():
    pol = resolve_llm_policy(_enabled(max_output_chars=5), {})
    huge = {"role": "assistant", "content": "y" * 100}
    out = make_policy_broker(pol, lambda m: huge)([])
    assert out["ok"] is False and "output size" in out["error"].lower()


# --- host ceiling always wins ---------------------------------------------

def test_host_ceiling_beats_manifest():
    # manifest asks for 100 calls, host caps at 1 → effective 1
    pol = resolve_llm_policy(_enabled(max_calls=100), {"agent_sandbox": {"llm": {"max_calls": 1}}})
    assert pol.max_calls == 1
    b = make_policy_broker(pol, lambda m: _OK)
    assert b([])["ok"] is True
    assert b([])["ok"] is False


def test_host_can_force_disable():
    pol = resolve_llm_policy(_enabled(), {"agent_sandbox": {"llm": {"enabled": False}}})
    assert pol.enabled is False


def test_host_may_raise_its_own_ceiling():
    # it's the host's own key — an explicit higher host ceiling is allowed
    pol = resolve_llm_policy(_enabled(max_calls=999), {"agent_sandbox": {"llm": {"max_calls": 50}}})
    assert pol.max_calls == 50


def test_defaults_applied_when_unset():
    pol = resolve_llm_policy(_enabled(), {})
    assert pol.max_calls == 20
    assert pol.max_input_chars == 24_000
    assert pol.max_output_chars == 24_000


# --- sanitized errors (no key / prompt / provider internals) ---------------

def test_provider_llmbrokererror_sanitized():
    pol = resolve_llm_policy(_enabled(), {})

    def boom(messages):
        raise LlmBrokerError("LLM provider call failed")

    out = make_policy_broker(pol, boom)([{"role": "user", "content": "secret-prompt"}])
    assert out["ok"] is False
    assert out["error"] == "LLM provider call failed"


def test_raw_exception_fully_generic_no_leak():
    pol = resolve_llm_policy(_enabled(), {})
    secret = "sk-LEAK-9999"

    def boom(messages):
        raise RuntimeError(f"401 key={secret} url=https://internal/v1")

    out = make_policy_broker(pol, boom)([{"role": "user", "content": "private-data"}])
    assert out["ok"] is False
    assert out["error"] == "LLM call failed"
    assert secret not in out["error"]
    assert "private-data" not in out["error"]
    assert "internal" not in out["error"]


def test_never_raises_on_failure():
    # the broker must signal failures structurally, never raise into host.run
    pol = resolve_llm_policy(_enabled(), {})
    b = make_policy_broker(pol, lambda m: (_ for _ in ()).throw(RuntimeError("x")))
    assert b([])["ok"] is False  # no exception propagated


def test_policy_dataclass_defaults_deny():
    assert LlmAccessPolicy().enabled is False
