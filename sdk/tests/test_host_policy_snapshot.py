"""Host-Policy F1 — single-snapshot fail-closed + MCP process compatibility.

Covers: normalize/read_snapshot; run_tool invalid-policy deny (config_error + one
sandbox_policy_check audit, no dispatch); skill/policy-free paths never read the
snapshot; the snapshot reader is called exactly once per top-level call; the MCP
pool reuses ONLY on full launch+boundary compatibility (restart on any change);
and static guards that no downstream runner re-reads host_trust_policy.
"""
from __future__ import annotations

import io
import json
import types

import pytest

from agentnode_sdk.config import normalize_host_trust_policy, read_host_trust_policy_snapshot
from agentnode_sdk.exceptions import ConfigurationError
from agentnode_sdk.runtimes.mcp_launch import (
    MCPProcessCompatibility,
    build_mcp_launch_plan,
)
from agentnode_sdk.sandbox.policy import HostTrustPolicyDecision
from agentnode_sdk.sandbox.types import SandboxRequiredError
from tests.hostpolicy import decision


# ---------------------------------------------------------------------------
# 1. normalize + snapshot
# ---------------------------------------------------------------------------

class TestNormalize:
    @pytest.mark.parametrize("value,expected", [
        ("default", "default"), ("curated_only", "curated_only"), ("none", "none"),
        ("NONE", "none"), (" none ", "none"), ("Curated_Only", "curated_only"),
    ])
    def test_valid(self, value, expected):
        assert normalize_host_trust_policy(value) == expected

    @pytest.mark.parametrize("value", [
        "strict", "curated-only", "", "   ", None, False, True, 0, 123, [], {}, ["x"], {"a": 1},
    ])
    def test_invalid_raises_without_leaking_raw(self, value):
        with pytest.raises(ConfigurationError) as ei:
            normalize_host_trust_policy(value)
        # raw offending value never appears in the message (except the empty string)
        assert value == "" or str(value) not in ei.value.message
        assert "default, curated_only, none" in ei.value.message

    def test_snapshot_missing_key_is_default(self, monkeypatch):
        monkeypatch.setattr("agentnode_sdk.config.load_config", lambda: {})  # no sandbox key
        assert read_host_trust_policy_snapshot() == "default"

    def test_snapshot_explicit_null_is_invalid(self, monkeypatch):
        monkeypatch.setattr("agentnode_sdk.config.load_config",
                            lambda: {"sandbox": {"host_trust_policy": None}})
        with pytest.raises(ConfigurationError):
            read_host_trust_policy_snapshot()

    def test_snapshot_valid(self, monkeypatch):
        monkeypatch.setattr("agentnode_sdk.config.load_config",
                            lambda: {"sandbox": {"host_trust_policy": "none"}})
        assert read_host_trust_policy_snapshot() == "none"


# ---------------------------------------------------------------------------
# 2. run_tool owner: invalid policy denied before dispatch; skill policy-free
# ---------------------------------------------------------------------------

def _fake_gate(entry, reason="verified"):
    report = types.SimpleNamespace(reason=reason, entry_status="verified",
                                   structure_status="ok", strict=False, allowed=True)
    def gate(slug, path, *, strict):
        return ({}, entry, report)
    return gate


def _prep_run_tool(monkeypatch, entry):
    import agentnode_sdk.runner as runner
    import agentnode_sdk.runtime_integrity as ri
    monkeypatch.setattr(ri, "gate_lock_integrity", _fake_gate(entry))
    monkeypatch.setattr(runner, "_maybe_refresh_trust", lambda s, e, p: e)
    return runner


class TestRunToolInvalidPolicy:
    def _invalid_snapshot(self, monkeypatch):
        def snap():
            raise ConfigurationError("INVALID_CONFIG",
                                     "Invalid sandbox.host_trust_policy configuration. "
                                     "Allowed values: default, curated_only, none.")
        monkeypatch.setattr("agentnode_sdk.config.read_host_trust_policy_snapshot", snap)

    def test_invalid_policy_denied_config_error_one_audit_no_dispatch(self, monkeypatch):
        runner = _prep_run_tool(monkeypatch, {
            "package_type": "toolpack", "runtime": "python", "trust_level": "trusted"})
        self._invalid_snapshot(monkeypatch)
        audits = []
        monkeypatch.setattr(runner, "audit_decision",
                            lambda d, e, s, **k: audits.append((e, d.action, d.reason, d.source, k.get("extra"))))
        dispatched = {}
        monkeypatch.setattr("agentnode_sdk.runtimes.python_runner.run_python",
                            lambda *a, **k: dispatched.setdefault("py", True))

        r = runner.run_tool("p")
        assert r.success is False and r.mode_used == "config_error"
        assert "default, curated_only, none" in (r.error or "")
        boundary = [a for a in audits if a[0] == "sandbox_policy_check"]
        assert len(boundary) == 1
        _, action, reason, source, extra = boundary[0]
        assert action == "deny" and reason == "invalid_host_trust_policy" and source == "sandbox_policy"
        assert extra["execution_boundary"] == "none" and extra["policy_recognized"] is False
        assert extra["runtime_kind"] == "python"
        # no sensitive fields, no raw value
        assert "goal" not in extra and "command" not in extra
        assert "py" not in dispatched  # never dispatched

    def test_skill_never_reads_snapshot(self, monkeypatch):
        runner = _prep_run_tool(monkeypatch, {
            "package_type": "skill", "runtime": "none", "trust_level": "curated"})
        calls = {"n": 0}
        def snap():
            calls["n"] += 1
            raise ConfigurationError("INVALID_CONFIG", "should not be called")
        monkeypatch.setattr("agentnode_sdk.config.read_host_trust_policy_snapshot", snap)
        r = runner.run_tool("sk")
        assert calls["n"] == 0                    # skill is policy-free
        assert r.mode_used == "none"              # unsupported runtime, unchanged

    def test_snapshot_reader_called_once_and_decision_threaded(self, monkeypatch):
        from agentnode_sdk.policy import PolicyResult
        runner = _prep_run_tool(monkeypatch, {
            "package_type": "toolpack", "runtime": "python", "trust_level": "trusted"})
        calls = {"n": 0}
        def snap():
            calls["n"] += 1
            return "default"
        monkeypatch.setattr("agentnode_sdk.config.read_host_trust_policy_snapshot", snap)
        # downstream must NOT read host_trust_policy again
        monkeypatch.setattr("agentnode_sdk.config.host_trust_policy",
                            lambda: (_ for _ in ()).throw(AssertionError("downstream re-read")))
        # allow past the policy/guard/input layers so dispatch is reached
        _allow = PolicyResult(action="allow", reason="ok", source="test")
        monkeypatch.setattr(runner, "check_run", lambda *a, **k: _allow)
        monkeypatch.setattr(runner, "check_risk_policies", lambda *a, **k: None)
        monkeypatch.setattr("agentnode_sdk.guard.check_action",
                            lambda *a, **k: types.SimpleNamespace(action="allow", action_types=[], risk_level="low", reason="", source="guard"))
        monkeypatch.setattr("agentnode_sdk.guard.check_rate_limit", lambda *a, **k: _allow)
        monkeypatch.setattr("agentnode_sdk.input_guard.validate_tool_input", lambda *a, **k: [])
        got = {}
        def fake_run_python(*a, _host_policy_decision, **k):
            got["decision"] = _host_policy_decision
            from agentnode_sdk.models import RunToolResult
            return RunToolResult(success=True, mode_used="subprocess")
        monkeypatch.setattr("agentnode_sdk.runtimes.python_runner.run_python", fake_run_python)
        r = runner.run_tool("p")
        assert r.success is True
        assert calls["n"] == 1                       # exactly one snapshot read
        assert got["decision"].policy == "default"   # decision threaded to the runner


# ---------------------------------------------------------------------------
# 3. MCP launch plan: policy-independent identity, deterministic, artifact-bound
# ---------------------------------------------------------------------------

class TestLaunchPlan:
    _ENTRY = {"trust_level": "curated", "mcp_command": ["npx", "-y", "@s/m@1"],
              "version": "1", "artifact_hash": "sha256:aaa", "mcp_env_keys": ["B", "A"]}

    def test_same_boundary_different_policy_string_reuses(self):
        p1 = build_mcp_launch_plan("m", self._ENTRY, decision("curated", "default"))
        p2 = build_mcp_launch_plan("m", self._ENTRY, decision("curated", "curated_only"))
        assert p1.compatibility == p2.compatibility  # both host → reuse allowed

    def test_deterministic(self):
        a = build_mcp_launch_plan("m", self._ENTRY, decision("curated", "default"))
        b = build_mcp_launch_plan("m", self._ENTRY, decision("curated", "default"))
        assert a.compatibility.launch_fingerprint == b.compatibility.launch_fingerprint

    @pytest.mark.parametrize("mutate", [
        {"mcp_command": ["npx", "other"]},
        {"version": "2"},
        {"artifact_hash": "sha256:bbb"},
        {"mcp_env_keys": ["A", "B", "C"]},
    ])
    def test_launch_change_changes_fingerprint(self, mutate):
        base = build_mcp_launch_plan("m", self._ENTRY, decision("curated", "default"))
        changed = build_mcp_launch_plan("m", {**self._ENTRY, **mutate}, decision("curated", "default"))
        assert changed.compatibility != base.compatibility

    def test_env_key_order_is_canonical(self):
        a = build_mcp_launch_plan("m", {**self._ENTRY, "mcp_env_keys": ["A", "B"]}, decision("curated", "default"))
        b = build_mcp_launch_plan("m", {**self._ENTRY, "mcp_env_keys": ["B", "A"]}, decision("curated", "default"))
        assert a.compatibility == b.compatibility  # unordered names → same

    def test_host_has_no_sandbox_profile_sandbox_does(self):
        host = build_mcp_launch_plan("m", {**self._ENTRY, "trust_level": "curated"}, decision("curated", "default"))
        sbx = build_mcp_launch_plan("m", {**self._ENTRY, "trust_level": "unverified"}, decision("unverified", "default"))
        assert host.compatibility.sandbox_profile_fingerprint is None
        assert sbx.compatibility.sandbox_profile_fingerprint is not None


# ---------------------------------------------------------------------------
# 4. MCP pool: reuse only on FULL compatibility; restart on any change
# ---------------------------------------------------------------------------

def _compat(boundary="host", trust="trusted", launch="L1", sbx=None):
    return MCPProcessCompatibility(
        execution_boundary=boundary, trust_level=trust, runtime_kind="mcp",
        launch_fingerprint=launch, sandbox_profile_fingerprint=sbx)


class _FakeProc:
    _ids = [0]

    def __init__(self, slug, command, trust_level=None, entry=None, mcp_consent_callback=None):
        self.slug = slug
        self.trust_level = trust_level
        self.compatibility = None
        self._alive = True
        self.start_calls = 0
        self.stop_calls = 0
        _FakeProc._ids[0] += 1
        self.pid = _FakeProc._ids[0]

    def start(self, timeout=10.0, env_keys=None, *, _host_policy_decision, launch_plan):
        self.start_calls += 1
        self.last_plan = launch_plan

    def health_check(self):
        return self._alive

    def stop(self):
        self.stop_calls += 1
        self._alive = False


def _fake_plan(compat):
    return types.SimpleNamespace(compatibility=compat, boundary=compat.execution_boundary,
                                 command=("cmd",))


@pytest.fixture
def pool(monkeypatch):
    from agentnode_sdk.runtimes import mcp_runner
    monkeypatch.setattr(mcp_runner, "MCPServerProcess", _FakeProc)
    return mcp_runner.MCPProcessPool()


def _get(pool, compat, trust="trusted", env_keys=None):
    return pool.get_or_start("m", ["cmd"], host_policy_decision=decision(trust),
                             launch_plan=_fake_plan(compat), env_keys=env_keys)


class TestPoolCompatibility:
    def test_identical_compatibility_reuses(self, pool):
        s1 = _get(pool, _compat())
        s2 = _get(pool, _compat())
        assert s1 is s2 and s1.start_calls == 1 and s1.stop_calls == 0

    def test_host_to_sandbox_restarts_no_old_reuse(self, pool):
        s1 = _get(pool, _compat(boundary="host"))
        s2 = _get(pool, _compat(boundary="sandbox", sbx="P1"))
        assert s2.pid != s1.pid and s1.stop_calls == 1 and s2.start_calls == 1
        assert pool._servers["m"] is s2                 # old never reused

    def test_sandbox_to_host_restarts(self, pool):
        s1 = _get(pool, _compat(boundary="sandbox", sbx="P1"))
        s2 = _get(pool, _compat(boundary="host"))
        assert s2.pid != s1.pid and s1.stop_calls == 1

    @pytest.mark.parametrize("a,b", [
        (_compat(launch="L1"), _compat(launch="L2")),                       # command/version/artifact
        (_compat(trust="trusted"), _compat(trust="curated")),               # trust
        (_compat(boundary="sandbox", sbx="P1"), _compat(boundary="sandbox", sbx="P2")),  # profile
    ])
    def test_any_identity_change_restarts(self, pool, a, b):
        s1 = _get(pool, a)
        s2 = _get(pool, b, trust=b.trust_level)
        assert s2.pid != s1.pid and s1.stop_calls == 1

    def test_legacy_process_without_compatibility_restarts(self, pool):
        s1 = _get(pool, _compat())
        s1.compatibility = None                          # simulate pre-upgrade process
        s2 = _get(pool, _compat())
        assert s2.pid != s1.pid and s1.stop_calls == 1

    def test_dead_process_restarts(self, pool):
        s1 = _get(pool, _compat())
        s1._alive = False
        s2 = _get(pool, _compat())
        assert s2.pid != s1.pid

    def test_credentialed_never_pooled(self, pool):
        s = _get(pool, _compat(boundary="sandbox", sbx="P1"), trust="verified", env_keys=["K"])
        assert "m" not in pool._servers and s.start_calls == 1

    def test_publish_only_after_successful_start(self, monkeypatch):
        from agentnode_sdk.runtimes import mcp_runner

        class _BoomProc(_FakeProc):
            def start(self, timeout=10.0, env_keys=None, *, _host_policy_decision, launch_plan):
                raise RuntimeError("start failed")
        monkeypatch.setattr(mcp_runner, "MCPServerProcess", _BoomProc)
        p = mcp_runner.MCPProcessPool()
        with pytest.raises(RuntimeError):
            p.get_or_start("m", ["cmd"], host_policy_decision=decision("trusted"),
                           launch_plan=_fake_plan(_compat()))
        assert "m" not in p._servers                     # no usable entry after failed start

    # --- Blocker 1: stop failure must be fail-closed (no parallel processes) ---
    def test_incompatible_stop_not_confirmed_fails_closed(self, pool):
        s1 = _get(pool, _compat(boundary="host"))
        # stop() runs but the process does NOT terminate (still healthy)
        s1.stop = lambda: setattr(s1, "stop_calls", s1.stop_calls + 1)
        before = _FakeProc._ids[0]
        with pytest.raises(SandboxRequiredError):
            _get(pool, _compat(boundary="sandbox", sbx="P1"))
        assert _FakeProc._ids[0] == before      # NO replacement process constructed
        assert "m" not in pool._servers         # pool empty for the slug
        assert s1.start_calls == 1              # old never restarted/reused

    def test_incompatible_stop_raises_fails_closed(self, pool):
        s1 = _get(pool, _compat(boundary="host"))
        def _boom():
            raise RuntimeError("stop failed")
        s1.stop = _boom
        before = _FakeProc._ids[0]
        with pytest.raises(RuntimeError):
            _get(pool, _compat(boundary="sandbox", sbx="P1"))
        assert _FakeProc._ids[0] == before and "m" not in pool._servers

    # --- Blocker 2: the SAME launch-plan object is threaded to start ---
    def test_launch_plan_object_threaded_to_start(self, pool):
        lp = _fake_plan(_compat())
        s = pool.get_or_start("m", ["cmd"], host_policy_decision=decision("trusted"),
                              launch_plan=lp)
        assert s.last_plan is lp                # exact object identity at start
        assert s.compatibility is lp.compatibility


# ---------------------------------------------------------------------------
# 5. Static guards — no downstream host_trust_policy / policy-matrix reads
# ---------------------------------------------------------------------------

def test_no_host_trust_policy_reads_in_downstream_runners():
    import pathlib
    import agentnode_sdk
    base = pathlib.Path(agentnode_sdk.__file__).parent
    for mod in ("runtimes/agent_runner.py", "runtimes/python_runner.py", "runtimes/mcp_runner.py"):
        src = (base / mod).read_text(encoding="utf-8")
        assert "host_trust_policy(" not in src, f"{mod} still reads host_trust_policy()"
        assert "import host_trust_policy" not in src, f"{mod} still imports host_trust_policy"
        assert "requires_sandbox_for_policy(" not in src, f"{mod} re-runs the policy matrix"


# ---------------------------------------------------------------------------
# 6. Blocker 3 — decision invariants (factory-shaped, tamper-resistant)
# ---------------------------------------------------------------------------

class TestDecisionInvariants:
    def test_valid_decision_from_enforce_accepted(self):
        d = decision("trusted", "curated_only")   # trusted+curated_only ⇒ sandbox
        assert d.sandbox_required is True and d.execution_boundary == "sandbox"

    @pytest.mark.parametrize("kwargs", [
        {"policy": "weird", "trust_level": "trusted", "sandbox_required": False, "execution_boundary": "host"},
        {"policy": "none", "trust_level": "trusted", "sandbox_required": False, "execution_boundary": "host"},
        {"policy": "default", "trust_level": "trusted", "sandbox_required": True, "execution_boundary": "host"},
        {"policy": "default", "trust_level": "trusted", "sandbox_required": True, "execution_boundary": "sandbox"},
        {"policy": "default", "trust_level": "trusted", "sandbox_required": False, "execution_boundary": "host", "policy_recognized": False},
    ])
    def test_contradictory_decision_rejected(self, kwargs):
        with pytest.raises(ValueError):
            HostTrustPolicyDecision(**kwargs)


# ---------------------------------------------------------------------------
# 7. Blocker 4 — backend + profile version bound into the sandbox fingerprint
# ---------------------------------------------------------------------------

class TestBackendBinding:
    _ENTRY = {"trust_level": "unverified", "mcp_command": ["npx", "m"],
              "version": "1", "artifact_hash": "sha256:aaa"}

    def test_backend_kind_changes_sandbox_fingerprint(self):
        d = decision("unverified")  # sandbox
        docker = build_mcp_launch_plan("m", self._ENTRY, d, backend_kind="docker")
        podman = build_mcp_launch_plan("m", self._ENTRY, d, backend_kind="podman")
        assert docker.compatibility != podman.compatibility          # ⇒ pool restart
        assert docker.compatibility.launch_fingerprint == podman.compatibility.launch_fingerprint

    def test_same_backend_reuses(self):
        d = decision("unverified")
        a = build_mcp_launch_plan("m", self._ENTRY, d, backend_kind="docker")
        b = build_mcp_launch_plan("m", self._ENTRY, d, backend_kind="docker")
        assert a.compatibility == b.compatibility


# ---------------------------------------------------------------------------
# 8. Blocker 2 — the launch plan (not a re-derivation) drives the real start
# ---------------------------------------------------------------------------

def test_launch_plan_command_drives_real_host_start(monkeypatch):
    from agentnode_sdk.runtimes import mcp_runner

    launched = {}

    class _P:
        def __init__(self, args, **k):
            launched["argv"] = args
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n")
            self.stderr = io.StringIO()
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def wait(self, timeout=None):
            self._alive = False
            return 0

        def kill(self):
            self._alive = False

    monkeypatch.setattr(mcp_runner.subprocess, "Popen", _P)
    entry = {"trust_level": "curated", "mcp_command": ["node", "server.js"],
             "version": "1", "artifact_hash": "h"}
    d = decision("curated")  # host
    plan = build_mcp_launch_plan("m", entry, d)
    # construct with a DELIBERATELY WRONG self.command — start must use plan.command
    proc = mcp_runner.MCPServerProcess("m", ["WRONG", "CMD"], trust_level="curated", entry=entry)
    proc.start(_host_policy_decision=d, launch_plan=plan)
    try:
        assert launched["argv"] == ["node", "server.js"]          # started == plan.command
        assert list(plan.command) == launched["argv"]             # == fingerprint input
    finally:
        proc.stop()


def test_start_refuses_plan_boundary_mismatch(monkeypatch):
    from agentnode_sdk.runtimes import mcp_runner
    entry = {"trust_level": "curated", "mcp_command": ["node"], "version": "1", "artifact_hash": "h"}
    d = decision("curated")  # host
    sandbox_plan = build_mcp_launch_plan("m", {**entry, "trust_level": "unverified"},
                                         decision("unverified"))  # sandbox plan
    proc = mcp_runner.MCPServerProcess("m", ["node"], trust_level="curated", entry=entry)
    with pytest.raises(RuntimeError):
        proc.start(_host_policy_decision=d, launch_plan=sandbox_plan)  # boundary mismatch
