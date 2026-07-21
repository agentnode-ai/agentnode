"""A1-E-Lock Layer 2, Weg B — host-agent ENTRYPOINT execution is structurally
fail-closed. This is the permanent contract test for:

  * the stable error code + message carried through RunToolResult / audit / run-log;
  * the single refusal chokepoint (``refuse_host_agent_execution``);
  * routing (trusted/curated host → unsupported; sandbox → sandbox; deny → deny);
  * the declarative sequential path (only run_tool, no foreign entrypoint import);
  * side-effect freedom (no import / spawn / reader / context / install reached);
  * no activation path (flag / env / config / monkeypatch cannot enable host exec);
  * platform-neutrality of the pure-Python gate.
"""
from __future__ import annotations

import json

import pytest

import agentnode_sdk.runtimes.agent_runner as ar
from agentnode_sdk.exceptions import (
    HOST_AGENT_EXECUTION_UNSUPPORTED,
    HOST_AGENT_UNSUPPORTED_MESSAGE,
    AgentNodeError,
    HostAgentExecutionUnsupported,
    refuse_host_agent_execution,
)
from agentnode_sdk.models import RunToolResult
from tests.hostpolicy import decision as _decision


@pytest.fixture(autouse=True)
def _permissive_guard(tmp_path, monkeypatch):
    cfg = {
        "version": "1", "trust": {"minimum_trust_level": "verified"},
        "guard": {k: "allow" for k in (
            "delete", "write_external", "execute", "credential_use", "network_egress",
            "write_local", "read", "compute", "unknown")},
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "0")
    from agentnode_sdk.guard import reset_guard_config_cache
    reset_guard_config_cache()
    yield
    reset_guard_config_cache()


def _agent_entry(trust_level="trusted", entrypoint="m:run", **agent_over):
    agent = {"entrypoint": entrypoint, "goal": "g",
             "tool_access": {"allowed_packages": None},
             "limits": {"max_iterations": 3, "max_tool_calls": 5, "max_runtime_seconds": 30}}
    agent.update(agent_over)
    return {"version": "1.0.0", "package_type": "agent", "runtime": "python",
            "entrypoint": "", "trust_level": trust_level, "agent": agent}


def _run(entry, policy="default", **kw):
    dec = _decision(entry.get("trust_level"), policy)
    return ar.run_agent("ag", entry=entry, _host_policy_decision=dec, **kw)


# ---------------------------------------------------------------------------
# 1. Exception + error-code contract (single source of truth)
# ---------------------------------------------------------------------------

def test_refuse_helper_raises_stable_exception():
    with pytest.raises(HostAgentExecutionUnsupported) as caught:
        refuse_host_agent_execution()
    assert caught.value.code == HOST_AGENT_EXECUTION_UNSUPPORTED
    assert caught.value.message == HOST_AGENT_UNSUPPORTED_MESSAGE
    assert HOST_AGENT_UNSUPPORTED_MESSAGE in str(caught.value)


def test_exception_is_agentnode_error():
    assert issubclass(HostAgentExecutionUnsupported, AgentNodeError)


def test_code_constant_value():
    assert HOST_AGENT_EXECUTION_UNSUPPORTED == "host_agent_execution_unsupported"


# ---------------------------------------------------------------------------
# 2. RunToolResult error_code serialization (backward compatible)
# ---------------------------------------------------------------------------

def test_runtoolresult_without_error_code_unchanged_serialization():
    r = RunToolResult(success=True, result={"x": 1})
    assert r.error_code is None
    assert "error_code" not in r.to_dict()


def test_runtoolresult_with_error_code_serialized():
    r = RunToolResult(success=False, error="msg", error_code="host_agent_execution_unsupported")
    d = r.to_dict()
    assert d["error_code"] == "host_agent_execution_unsupported"
    assert d["error"] == "msg"


def test_unsupported_result_shape():
    r = _run(_agent_entry())
    assert r.success is False
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED   # stable code here
    assert r.mode_used == "agent"                             # NOT the error code
    assert r.error and r.error != r.error_code                # message != code
    assert HOST_AGENT_EXECUTION_UNSUPPORTED not in (r.error or "")


# ---------------------------------------------------------------------------
# 3. Routing matrix (real seams)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trust", ["trusted", "curated"])
def test_host_tier_unsupported(trust):
    r = _run(_agent_entry(trust_level=trust), policy="default")
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED


def test_curated_only_policy_host_tiers():
    # curated stays host under curated_only → unsupported (trusted would be sandboxed)
    r = _run(_agent_entry(trust_level="curated"), policy="curated_only")
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED


def test_sandbox_routed_never_reaches_chokepoint(monkeypatch):
    seen = {"sandbox": 0}

    def spy(slug, entry, agent_config, *, goal=None, run_id=None, **kw):
        seen["sandbox"] += 1
        return RunToolResult(success=True, result="sb", mode_used="agent_sandbox", run_id=run_id)
    monkeypatch.setattr("agentnode_sdk.runtimes.agent_sandbox.run_agent_sandboxed", spy)
    # trip the chokepoint if it is ever reached
    monkeypatch.setattr(ar, "_host_agent_unsupported",
                        lambda *a, **k: pytest.fail("chokepoint reached on sandbox route"))
    monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "1")   # community flag on → sandbox
    r = _run(_agent_entry(trust_level="unverified"))
    assert r.mode_used == "agent_sandbox"
    assert seen["sandbox"] == 1


def test_community_routes_to_sandbox_never_host():
    # A community tier's decision is sandbox (not host) regardless of runtime
    # availability — so it can never fall through to the host-unsupported chokepoint.
    # When the sandbox runtime is unavailable, run_tool raises SandboxRequiredError
    # UPSTREAM (before run_agent) — it is never substituted by host_agent_execution_unsupported.
    from agentnode_sdk.sandbox.policy import requires_sandbox_for_policy
    assert requires_sandbox_for_policy("unverified", "default") is True
    assert requires_sandbox_for_policy("verified", "default") is True


def test_deny_not_chokepoint():
    # verified is below the host minimum and (flag off) not sandboxed → trust-level deny
    r = _run(_agent_entry(trust_level="verified"))
    assert r.success is False
    assert r.error_code != HOST_AGENT_EXECUTION_UNSUPPORTED
    assert "trust level" in (r.error or "").lower()


# ---------------------------------------------------------------------------
# 4. Sequential contract
# ---------------------------------------------------------------------------

def test_sequential_only_calls_run_tool_source():
    import inspect
    src = inspect.getsource(ar._run_sequential)
    assert "run_tool(" in src
    for forbidden in ("import_module", "_load_agent_entrypoint", "entrypoint"):
        assert forbidden not in src


def test_sequential_non_agent_step_runs_via_run_tool(monkeypatch):
    calls = []
    monkeypatch.setattr("agentnode_sdk.runner.run_tool",
                        lambda slug, tool_name=None, **k: calls.append(slug) or
                        RunToolResult(success=True, result={"ok": slug}))
    entry = _agent_entry()
    entry["agent"]["orchestration"] = {"mode": "sequential",
                                        "steps": [{"name": "s1", "tool": "csv-analyzer-pack"}]}
    entry["agent"]["tool_access"] = {"allowed_packages": ["csv-analyzer-pack"]}
    r = _run(entry)
    assert r.success is True and calls == ["csv-analyzer-pack"]


def test_sequential_host_agent_step_is_unsupported(monkeypatch):
    # a step whose tool is itself a host agent → run_tool → run_agent → unsupported
    def fake_run_tool(slug, tool_name=None, **k):
        return RunToolResult(success=False, error="msg",
                             error_code=HOST_AGENT_EXECUTION_UNSUPPORTED, mode_used="agent")
    monkeypatch.setattr("agentnode_sdk.runner.run_tool", fake_run_tool)
    entry = _agent_entry()
    entry["agent"]["orchestration"] = {"mode": "sequential",
                                        "steps": [{"name": "s1", "tool": "some-host-agent"}]}
    entry["agent"]["tool_access"] = {"allowed_packages": ["some-host-agent"]}
    r = _run(entry)
    assert r.success is False   # the step failed with the unsupported code


# ---------------------------------------------------------------------------
# 5. Negative side-effect matrix — NOTHING dangerous runs before the refusal
# ---------------------------------------------------------------------------

def test_no_side_effects_on_host_path(monkeypatch):
    import importlib
    import multiprocessing
    tripped = []

    def trip(name):
        def _f(*a, **k):
            tripped.append(name)
            raise AssertionError(f"tripwire reached: {name}")
        return _f

    monkeypatch.setattr(importlib, "import_module", trip("import_module"))
    monkeypatch.setattr(multiprocessing, "get_context", trip("get_context"))
    monkeypatch.setattr(multiprocessing, "Process", trip("Process"))
    monkeypatch.setattr(ar, "AgentContext", trip("AgentContext"))
    monkeypatch.setattr(ar, "_auto_detect_llm", trip("auto_detect_llm"))
    monkeypatch.setattr(ar, "_eager_install_deps", trip("eager_install"))
    import agentnode_sdk._env_rwlock as rw
    monkeypatch.setattr(rw, "env_read_lock", trip("env_read_lock"))
    import agentnode_sdk.client as _client
    monkeypatch.setattr(_client.AgentNodeClient, "install", trip("client.install"))

    r = _run(_agent_entry(trust_level="trusted"))
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED
    assert tripped == []


# ---------------------------------------------------------------------------
# 6. No activation path
# ---------------------------------------------------------------------------

def test_invented_flag_does_not_enable(monkeypatch):
    import agentnode_sdk.exceptions as ex
    monkeypatch.setattr(ex, "_HOST_AGENT_EXEC_ENABLED", True, raising=False)
    monkeypatch.setattr(ar, "_HOST_AGENT_EXEC_ENABLED", True, raising=False)
    r = _run(_agent_entry())
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED


@pytest.mark.parametrize("policy", ["default", "curated_only"])
def test_no_policy_enables_host(policy):
    r = _run(_agent_entry(trust_level="curated"), policy=policy)
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED


def test_env_vars_do_not_enable(monkeypatch):
    for name in ("AGENTNODE_HOST_AGENT_EXEC", "AGENTNODE_ENABLE_HOST_AGENT",
                 "HOST_AGENT_EXEC_ENABLED", "AGENTNODE_AGENT_HOST"):
        monkeypatch.setenv(name, "1")
    r = _run(_agent_entry())
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED


def test_deleted_prototype_module_not_importable():
    with pytest.raises(ModuleNotFoundError):
        __import__("agentnode_sdk.runtimes._agent_exec")


# ---------------------------------------------------------------------------
# 7. Platform neutrality — pure-Python gate, no platform activation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fake_platform", ["linux", "darwin", "win32"])
def test_platform_neutral(monkeypatch, fake_platform):
    monkeypatch.setattr("sys.platform", fake_platform)
    r = _run(_agent_entry())
    assert r.error_code == HOST_AGENT_EXECUTION_UNSUPPORTED
