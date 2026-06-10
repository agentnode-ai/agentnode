"""C1/C2 — host-credential LLM broker policy. Docker-free; base broker is a fake.

Proves: default-DENY (no llm_access / enabled:false → refused), opt-in allow within
caps, per-run max_calls, max_input_chars, max_output_chars, host-config ceiling
beats the manifest, and that provider/host errors come back as sanitized per-call
errors (no key / no prompt / no raw provider internals). C2 adds: allowed_models
resolution (intersection, []=refuse-all), the conditional kwarg pass-through,
usage counters for audit, and the publisher template example.
"""
from __future__ import annotations

from agentnode_sdk.runtimes.agent_llm_broker import LlmBrokerError, LlmModelNotAllowedError
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
    assert LlmAccessPolicy().allowed_models is None


# --- allowed_models (C2, defense-in-depth) ----------------------------------

def test_allowed_models_absent_is_unrestricted():
    assert resolve_llm_policy(_enabled(), {}).allowed_models is None


def test_allowed_models_manifest_only():
    pol = resolve_llm_policy(_enabled(allowed_models=["a", "b"]), {})
    assert pol.allowed_models == frozenset({"a", "b"})


def test_allowed_models_host_only():
    pol = resolve_llm_policy(
        _enabled(), {"agent_sandbox": {"llm": {"allowed_models": ["h"]}}})
    assert pol.allowed_models == frozenset({"h"})


def test_allowed_models_intersection():
    # both sides set → both must allow (the set-form of "host ceiling wins")
    pol = resolve_llm_policy(
        _enabled(allowed_models=["a", "b"]),
        {"agent_sandbox": {"llm": {"allowed_models": ["b", "c"]}}},
    )
    assert pol.allowed_models == frozenset({"b"})


def test_allowed_models_empty_list_refuses_all():
    # explicit [] = no model is acceptable (tool_access.allowed_packages convention)
    pol = resolve_llm_policy(_enabled(allowed_models=[]), {})
    assert pol.allowed_models == frozenset()


def test_allowed_models_non_list_treated_as_absent():
    pol = resolve_llm_policy(_enabled(allowed_models="gpt-4o-mini"), {})
    assert pol.allowed_models is None


def test_allowed_models_kwarg_passed_only_when_set():
    # unrestricted → plain single-arg call (existing fakes/monkeypatches keep working)
    seen = {}

    def base(messages, **kw):
        seen.update(kw)
        return _OK

    out = make_policy_broker(resolve_llm_policy(_enabled(), {}), base)([])
    assert out["ok"] is True
    assert "allowed_models" not in seen
    # restricted → the effective frozenset is forwarded
    pol = resolve_llm_policy(_enabled(allowed_models=["m"]), {})
    out = make_policy_broker(pol, base)([])
    assert out["ok"] is True
    assert seen.get("allowed_models") == frozenset({"m"})


def test_model_not_allowed_generic_error_and_counter():
    pol = resolve_llm_policy(_enabled(allowed_models=["only-this"]), {})

    def base(messages, **kw):
        raise LlmModelNotAllowedError("secret-host-model")

    b = make_policy_broker(pol, base)
    out = b([])
    assert out["ok"] is False
    assert "not allowed" in out["error"].lower()
    assert "secret-host-model" not in out["error"]   # host model name never reaches the sandbox
    assert b.usage["refused_model"] == 1
    assert b.usage["model"] == "secret-host-model"   # host-side only, for audit


# --- usage counters / audit surface (C2) -------------------------------------

def test_usage_counters_and_policy_exposed():
    pol = resolve_llm_policy(_enabled(max_calls=2, max_input_chars=10), {})
    b = make_policy_broker(pol, lambda m: _OK)
    assert b.policy is pol
    assert b([])["ok"] is True                                 # ok
    assert b([{"role": "user", "content": "x" * 50}])["ok"] is False  # input cap
    assert b([])["ok"] is False                                # over max_calls
    u = b.usage
    assert u["requests"] == 3 and u["calls"] == 3
    assert u["ok"] == 1 and u["refused_input"] == 1 and u["refused_limit"] == 1


def test_usage_counts_disabled_refusals():
    b = make_policy_broker(resolve_llm_policy({}, {}), lambda m: _OK)
    b([])
    b([])
    assert b.usage["refused_disabled"] == 2
    assert b.usage["calls"] == 0           # denied requests never count as calls


def test_usage_counts_output_and_provider_errors():
    pol = resolve_llm_policy(_enabled(max_output_chars=5), {})
    b = make_policy_broker(pol, lambda m: {"role": "assistant", "content": "y" * 100})
    assert b([])["ok"] is False
    assert b.usage["refused_output"] == 1

    b2 = make_policy_broker(resolve_llm_policy(_enabled(), {}),
                            lambda m: (_ for _ in ()).throw(RuntimeError("x")))
    assert b2([])["ok"] is False
    assert b2.usage["provider_errors"] == 1


def test_usage_never_contains_message_content():
    pol = resolve_llm_policy(_enabled(), {})
    b = make_policy_broker(pol, lambda m: _OK)
    b([{"role": "user", "content": "SENTINEL-PROMPT"}])
    assert "SENTINEL-PROMPT" not in repr(b.usage)


# --- publisher template (C2) --------------------------------------------------

def test_agent_template_includes_llm_access(tmp_path):
    import yaml

    from agentnode_sdk.cli.init import scaffold_package

    scaffold_package("agent", tmp_path, package_id="my-agent", name="My Agent")
    raw = (tmp_path / "agentnode.yaml").read_text(encoding="utf-8")
    manifest = yaml.safe_load(raw)
    acc = manifest["agent"]["llm_access"]
    assert acc["enabled"] is False                 # opt-in: default off
    assert acc["max_calls"] == 20
    assert acc["max_input_chars"] == 24000
    assert acc["max_output_chars"] == 24000
    assert "allowed_models" in raw                 # documented (commented) option
    assert "opt-in" in raw                         # publisher-facing wording
    assert "ALWAYS" in raw and "ceiling" in raw    # host ceiling wins
