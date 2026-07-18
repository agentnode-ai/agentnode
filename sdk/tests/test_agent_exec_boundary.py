"""Agent-Exec D1 — pre-import host-execution observability.

OBSERVABILITY ONLY. This slice is NOT an RCE mitigation: it reduces no host
capability, isolates no import-time code, verifies no runtime artifact, and makes
no allow/deny/routing decision. These tests pin the contract:

  * before ANY foreign agent code is imported on the host, ``run_agent`` emits
    EXACTLY one warning + one best-effort ``agent_execution_boundary`` audit that
    reflect the ALREADY-MADE production routing decision (no second policy);
  * the sandbox and deny routes emit NEITHER a host warning nor a boundary event;
  * the audit carries only safe trust/boundary/isolation facts (never goal,
    kwargs, entrypoint, module, paths, env, secrets, hashes, signatures, tool
    results);
  * neither the warning nor an audit failure can turn the allow into a deny or a
    second event.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.runtimes import agent_runner
from agentnode_sdk.runtimes.agent_runner import run_agent


def _agent_entry(trust_level="trusted", isolation=None, entrypoint="test_module:agent_func", **overrides):
    """Minimal agent entry that reaches run_agent's host path. allowed_packages
    is None so no eager install runs; the LLM warning never fires (no llm config)."""
    agent = {
        "entrypoint": entrypoint,
        "goal": "Test goal",
        "tool_access": {"allowed_packages": None},
        "limits": {"max_iterations": 5, "max_tool_calls": 10, "max_runtime_seconds": 30},
        "termination": {"stop_on_final_answer": True, "stop_on_consecutive_errors": 3},
    }
    if isolation is not None:
        agent["isolation"] = isolation
    base = {
        "version": "1.0.0", "package_type": "agent", "runtime": "python",
        "entrypoint": "", "trust_level": trust_level, "agent": agent,
    }
    base.update(overrides)
    return base


class _Harness:
    """Records ordered lifecycle markers + captured audits/warnings for one run.

    Patches the module seams so no real import/execution happens: the host path
    runs to completion deterministically and every observable event is recorded
    in call order.
    """

    def __init__(self, monkeypatch, *, import_raises=False, boundary_audit_raises=False):
        self.events: list[str] = []      # ordered: "warning" | "audit:<event>" | "import" | "execute:<mode>"
        self.audits: list[dict] = []     # full boundary/agent_run payloads
        self.warnings: list[str] = []    # warning message strings
        self._mp = monkeypatch
        self._import_raises = import_raises
        self._boundary_audit_raises = boundary_audit_raises

    def install(self):
        mp = self._mp
        # Default routing surface: community flag OFF + default policy. Tests that
        # exercise sandbox/other policies override these afterwards.
        mp.setenv("AGENTNODE_AGENT_SANDBOX", "0")
        mp.setattr("agentnode_sdk.config.load_config", lambda: {})
        mp.setattr("agentnode_sdk.config.host_trust_policy", lambda: "default")

        def _warn(msg, *a, **k):
            self.events.append("warning")
            self.warnings.append((msg % a) if a else msg)
        mp.setattr(agent_runner.logger, "warning", _warn)

        def _audit(result, event_type, slug, **kw):
            self.events.append(f"audit:{event_type}")
            self.audits.append({
                "event": event_type, "slug": slug,
                "action": result.action, "reason": result.reason, "source": result.source,
                "trust_level": kw.get("trust_level"), "run_id": kw.get("run_id"),
                "extra": kw.get("extra"),
            })
            if event_type == "agent_execution_boundary" and self._boundary_audit_raises:
                raise RuntimeError("audit backend down")
        mp.setattr(agent_runner, "audit_decision", _audit)

        def _load(slug, entrypoint):
            self.events.append("import")
            if self._import_raises:
                raise ImportError("no module named test_module")
            return lambda context, **kwargs: {"ok": True}
        mp.setattr(agent_runner, "_load_agent_entrypoint", _load)

        def _exec_thread(func, context, kwargs, *, timeout):
            self.events.append("execute:thread")
            return {"ok": "thread"}

        def _exec_process(func, context, kwargs, *, timeout):
            self.events.append("execute:process")
            return {"ok": "process"}
        mp.setattr(agent_runner, "_execute_with_timeout", _exec_thread)
        mp.setattr(agent_runner, "_execute_with_process", _exec_process)
        return self

    @property
    def boundary_audits(self):
        return [a for a in self.audits if a["event"] == "agent_execution_boundary"]

    @property
    def agent_run_audits(self):
        return [a for a in self.audits if a["event"] == "agent_run"]


# ---------------------------------------------------------------------------
# 1. Timing — warning + audit strictly BEFORE the first host import
# ---------------------------------------------------------------------------

class TestTimingBeforeImport:
    def test_thread_order_warning_audit_import_execute(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        r = run_agent("a", entry=_agent_entry(trust_level="trusted", isolation="thread"))
        assert r.success is True
        # exact prefix order per the D1 contract
        assert h.events[:4] == ["warning", "audit:agent_execution_boundary", "import", "execute:thread"]
        assert h.events.index("warning") < h.events.index("import")
        assert h.events.index("audit:agent_execution_boundary") < h.events.index("import")

    def test_process_order_import_in_parent_before_isolation(self, monkeypatch):
        """Process mode does NOT protect import-time code: the host import still
        precedes execution. The boundary warning/audit precede that import."""
        h = _Harness(monkeypatch).install()
        r = run_agent("a", entry=_agent_entry(trust_level="trusted", isolation="process"))
        assert r.success is True
        assert h.events[:4] == ["warning", "audit:agent_execution_boundary", "import", "execute:process"]

    def test_import_failure_still_warned_and_audited_before(self, monkeypatch):
        """If the host import raises, the warning + boundary audit were already
        emitted exactly once before it, the existing load_failed audit is kept, and
        the agent function never runs."""
        h = _Harness(monkeypatch, import_raises=True).install()
        r = run_agent("a", entry=_agent_entry(trust_level="trusted"))
        assert r.success is False and "Failed to load agent entrypoint" in (r.error or "")
        assert h.warnings and len(h.warnings) == 1
        assert len(h.boundary_audits) == 1
        # order: warning, boundary audit, import (which raised), then load_failed audit
        assert h.events[:3] == ["warning", "audit:agent_execution_boundary", "import"]
        assert "execute:thread" not in h.events and "execute:process" not in h.events
        loadfail = [a for a in h.agent_run_audits if a["action"] == "deny"]
        assert loadfail and loadfail[0]["reason"].startswith("load_failed")


# ---------------------------------------------------------------------------
# 2. Exactly-once contract across trust × isolation
# ---------------------------------------------------------------------------

class TestExactlyOnce:
    @pytest.mark.parametrize("trust", ["trusted", "curated"])
    @pytest.mark.parametrize("isolation", ["thread", "process"])
    def test_one_warning_one_boundary_one_endevent(self, monkeypatch, trust, isolation):
        h = _Harness(monkeypatch).install()
        # curated stays host only under default/curated_only; default is fine for both
        r = run_agent("a", entry=_agent_entry(trust_level=trust, isolation=isolation))
        assert r.success is True
        assert len(h.warnings) == 1
        assert len(h.boundary_audits) == 1
        # exactly one terminal agent_run event (success)
        assert len(h.agent_run_audits) == 1
        assert h.agent_run_audits[0]["action"] == "allow"
        # boundary carries the right trust + isolation
        b = h.boundary_audits[0]
        assert b["trust_level"] == trust
        assert b["extra"]["isolation"] == isolation


# ---------------------------------------------------------------------------
# 3. Sandbox + deny routes: NO host warning, NO boundary event, NO host import
# ---------------------------------------------------------------------------

class TestSandboxAndDenyEmitNothing:
    def _spy_sandbox(self, monkeypatch):
        def spy(slug, entry, agent_config, *, goal=None, run_id=None, **kwargs):
            return RunToolResult(success=False, error="spy", mode_used="agent_sandbox", run_id=run_id)
        monkeypatch.setattr("agentnode_sdk.runtimes.agent_sandbox.run_agent_sandboxed", spy)

    def _assert_no_host_observability(self, h):
        assert h.warnings == []
        assert h.boundary_audits == []
        assert "import" not in h.events
        assert "execute:thread" not in h.events and "execute:process" not in h.events

    def test_community_flag_on_sandboxed(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "1")
        self._spy_sandbox(monkeypatch)
        r = run_agent("a", entry=_agent_entry(trust_level="verified"))
        assert r.mode_used == "agent_sandbox"
        self._assert_no_host_observability(h)

    def test_trusted_curated_only_sandboxed(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: "curated_only")
        self._spy_sandbox(monkeypatch)
        r = run_agent("a", entry=_agent_entry(trust_level="trusted"))
        assert r.mode_used == "agent_sandbox"
        self._assert_no_host_observability(h)

    def test_trusted_none_sandboxed(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: "none")
        self._spy_sandbox(monkeypatch)
        r = run_agent("a", entry=_agent_entry(trust_level="trusted"))
        assert r.mode_used == "agent_sandbox"
        self._assert_no_host_observability(h)

    def test_curated_none_sandboxed(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: "none")
        self._spy_sandbox(monkeypatch)
        r = run_agent("a", entry=_agent_entry(trust_level="curated"))
        assert r.mode_used == "agent_sandbox"
        self._assert_no_host_observability(h)

    def test_unverified_flag_off_denied(self, monkeypatch):
        h = _Harness(monkeypatch).install()  # flag off
        r = run_agent("a", entry=_agent_entry(trust_level="unverified"))
        assert r.success is False and "trust level" in (r.error or "")
        self._assert_no_host_observability(h)
        # the existing trust-deny audit is preserved and unchanged
        deny = [a for a in h.agent_run_audits if a["action"] == "deny"]
        assert deny and "below 'trusted'" in deny[0]["reason"]

    def test_verified_flag_off_denied(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        r = run_agent("a", entry=_agent_entry(trust_level="verified"))
        assert r.success is False and "trust level" in (r.error or "")
        self._assert_no_host_observability(h)


# ---------------------------------------------------------------------------
# 4. Policy values — recognized, and the fail-open unknown case made VISIBLE
# ---------------------------------------------------------------------------

class TestPolicyValues:
    def _boundary(self, monkeypatch, *, policy, trust="trusted"):
        h = _Harness(monkeypatch).install()
        monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: policy)
        r = run_agent("a", entry=_agent_entry(trust_level=trust))
        return h, r

    def test_default_recognized(self, monkeypatch):
        h, r = self._boundary(monkeypatch, policy="default")
        assert r.success is True
        b = h.boundary_audits[0]
        assert b["extra"]["host_trust_policy"] == "default"
        assert b["extra"]["policy_recognized"] is True
        assert b["reason"] == "host_execution_selected"

    def test_curated_only_curated_stays_host_recognized(self, monkeypatch):
        h, r = self._boundary(monkeypatch, policy="curated_only", trust="curated")
        assert r.success is True
        b = h.boundary_audits[0]
        assert b["extra"]["host_trust_policy"] == "curated_only"
        assert b["extra"]["policy_recognized"] is True
        assert b["reason"] == "host_execution_selected"

    # Reachable unrecognized values are strings (host_trust_policy() -> str). A
    # non-string crashes the PRE-EXISTING requires_sandbox_for_policy routing call
    # before D1 runs — that is not D1's concern (candidate for the fail-closed
    # policy slice), so it is deliberately not exercised here.
    @pytest.mark.parametrize("policy", ["weird_value", "unknown_policy", ""])
    def test_unknown_policy_host_fallback_made_visible(self, monkeypatch, policy):
        """D1 does NOT change the fail-open routing for unrecognized policy values
        (a host-tier agent still runs on the host), but it makes that fallback
        explicit: policy_recognized=False, host_trust_policy='unknown' (no raw
        value leaked), reason='unrecognized_policy_host_fallback'."""
        h, r = self._boundary(monkeypatch, policy=policy)
        assert r.success is True  # routing unchanged: still host
        b = h.boundary_audits[0]
        assert b["extra"]["policy_recognized"] is False
        assert b["extra"]["host_trust_policy"] == "unknown"
        assert b["extra"]["routing_reason"] == "unrecognized_policy_host_fallback"
        # the raw unrecognized value is never carried into the audit fields
        assert policy == "" or str(policy) not in json.dumps(b["extra"])


# ---------------------------------------------------------------------------
# 5. Audit fields — exactly the safe set, no sensitive content
# ---------------------------------------------------------------------------

class TestAuditFields:
    def test_only_safe_fields_present(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        run_agent(
            "a",
            entry=_agent_entry(trust_level="trusted", isolation="process"),
            goal="SENTINEL_GOAL_DO_NOT_AUDIT",
            secret_kwarg="SENTINEL_SECRET",
        )
        b = h.boundary_audits[0]
        assert b["action"] == "allow"
        assert b["source"] == "agent_runner"
        assert b["trust_level"] == "trusted"
        assert set(b["extra"].keys()) == {
            "host_trust_policy", "execution_boundary", "isolation",
            "sandbox_required", "sandbox_enabled", "routing_reason", "policy_recognized",
        }
        assert b["extra"]["execution_boundary"] == "host"
        assert b["extra"]["isolation"] == "process"
        assert b["extra"]["sandbox_required"] is False
        # no sensitive content anywhere in the boundary record
        blob = json.dumps(b)
        for sentinel in ("SENTINEL_GOAL", "SENTINEL_SECRET", "test_module", "agent_func"):
            assert sentinel not in blob
        for key in ("goal", "kwargs", "entrypoint", "module", "path", "env",
                    "hash", "signature", "tool"):
            assert key not in b["extra"]


# ---------------------------------------------------------------------------
# 6. Failure isolation — observability never flips allow→deny or emits twice
# ---------------------------------------------------------------------------

class TestFailureIsolation:
    def test_boundary_audit_failure_does_not_break_run(self, monkeypatch):
        h = _Harness(monkeypatch, boundary_audit_raises=True).install()
        r = run_agent("a", entry=_agent_entry(trust_level="trusted"))
        # decision unchanged: the host run still completed
        assert r.success is True
        # warning still emitted exactly once; import happened once; no second boundary
        assert len(h.warnings) == 1
        assert h.events.count("import") == 1
        assert len(h.boundary_audits) == 1
        assert "execute:thread" in h.events

    def test_warning_api_never_raises_even_with_failing_handler(self, monkeypatch):
        """Proof that the warning path cannot convert allow→deny. A logging handler
        whose emit() raises without handleError DOES propagate through
        logger.warning (stdlib does not wrap it), so the helper wraps the warning
        best-effort: the failing handler must not escape _emit_...()."""
        lg = logging.getLogger("agentnode.agent_runner")

        class _BoomHandler(logging.Handler):
            def emit(self, record):
                raise RuntimeError("handler boom")

        h = _BoomHandler()
        lg.addHandler(h)
        monkeypatch.setattr(logging, "raiseExceptions", False)
        try:
            # must return normally despite the failing handler
            agent_runner._emit_host_execution_observability(
                slug="a", trust_level="trusted", host_trust_policy="default",
                isolation="thread", sandbox_required=False, sandbox_enabled=False,
                routing_reason="host_execution_selected", policy_recognized=True,
            )
        finally:
            lg.removeHandler(h)


# ---------------------------------------------------------------------------
# 7. Sequential agents import no foreign host code → no host observability
# ---------------------------------------------------------------------------

class TestSequentialNoObservability:
    def test_sequential_host_agent_not_warned(self, monkeypatch):
        h = _Harness(monkeypatch).install()
        marker = RunToolResult(success=True, result={"seq": True}, mode_used="agent")
        monkeypatch.setattr(agent_runner, "_run_sequential",
                            lambda *a, **k: marker)
        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["orchestration"] = {"mode": "sequential", "steps": []}
        r = run_agent("a", entry=entry)
        assert r.result == {"seq": True}
        # declarative pipeline: no foreign host import → no host warning/boundary
        assert h.warnings == []
        assert h.boundary_audits == []
        assert "import" not in h.events


# ---------------------------------------------------------------------------
# 7b. Warning remediation must match the real routing matrix (trust-specific)
# ---------------------------------------------------------------------------

class TestWarningRemediationTrustSpecific:
    """The remediation named in the host warning must actually sandbox the shown
    trust level: curated_only sandboxes trusted but NOT curated; only none
    sandboxes curated; the community flag AGENTNODE_AGENT_SANDBOX sandboxes
    neither host tier and must never be offered."""

    def _host_warning(self, monkeypatch, *, trust, policy="default", flag=None):
        h = _Harness(monkeypatch).install()
        if flag is not None:
            monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", flag)
        monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: policy)
        r = run_agent("a", entry=_agent_entry(trust_level=trust))
        return h, r

    def test_trusted_remediation_names_curated_only_and_none(self, monkeypatch):
        h, r = self._host_warning(monkeypatch, trust="trusted")
        assert r.success is True
        assert len(h.warnings) == 1
        w = h.warnings[0]
        assert "curated_only" in w and "none" in w
        assert "enable the agent sandbox" not in w
        assert "AGENTNODE_AGENT_SANDBOX" not in w

    def test_curated_remediation_names_none_only(self, monkeypatch):
        # curated is NOT sandboxed by curated_only → it must not be offered as a
        # sufficient remediation; only none is correct for curated.
        h, r = self._host_warning(monkeypatch, trust="curated")
        assert r.success is True
        assert len(h.warnings) == 1
        w = h.warnings[0]
        assert "none" in w
        assert "curated_only" not in w
        assert "enable the agent sandbox" not in w
        assert "AGENTNODE_AGENT_SANDBOX" not in w


# ---------------------------------------------------------------------------
# 7c. Routing matrix pinned — proves the warning describes the real routing
# ---------------------------------------------------------------------------

class TestRoutingMatrixUnchanged:
    def _run(self, monkeypatch, *, trust, policy, flag=None):
        h = _Harness(monkeypatch).install()
        if flag is not None:
            monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", flag)
        monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: policy)
        seen = {}

        def spy(slug, entry, agent_config, *, goal=None, run_id=None, **kwargs):
            seen["sandboxed"] = True
            return RunToolResult(success=False, error="spy", mode_used="agent_sandbox", run_id=run_id)
        monkeypatch.setattr("agentnode_sdk.runtimes.agent_sandbox.run_agent_sandboxed", spy)
        r = run_agent("a", entry=_agent_entry(trust_level=trust))
        return h, r, seen

    def test_trusted_curated_only_sandboxed_no_host_warning(self, monkeypatch):
        h, r, seen = self._run(monkeypatch, trust="trusted", policy="curated_only")
        assert seen.get("sandboxed") is True and r.mode_used == "agent_sandbox"
        assert h.warnings == [] and h.boundary_audits == []

    def test_curated_curated_only_host_with_curated_warning(self, monkeypatch):
        h, r, seen = self._run(monkeypatch, trust="curated", policy="curated_only")
        assert seen.get("sandboxed") is None and r.success is True   # host route
        assert len(h.warnings) == 1
        w = h.warnings[0]
        assert "none" in w and "curated_only" not in w

    def test_curated_none_sandboxed_no_host_warning(self, monkeypatch):
        h, r, seen = self._run(monkeypatch, trust="curated", policy="none")
        assert seen.get("sandboxed") is True and r.mode_used == "agent_sandbox"
        assert h.warnings == [] and h.boundary_audits == []

    def test_flag_on_trusted_default_stays_host_with_trusted_warning(self, monkeypatch):
        h, r, seen = self._run(monkeypatch, trust="trusted", policy="default", flag="1")
        assert seen.get("sandboxed") is None and r.success is True
        assert len(h.warnings) == 1
        w = h.warnings[0]
        assert "curated_only" in w and "none" in w
        assert "enable the agent sandbox" not in w

    def test_flag_on_curated_default_stays_host_with_curated_warning(self, monkeypatch):
        h, r, seen = self._run(monkeypatch, trust="curated", policy="default", flag="1")
        assert seen.get("sandboxed") is None and r.success is True
        assert len(h.warnings) == 1
        w = h.warnings[0]
        assert "none" in w and "curated_only" not in w
        assert "enable the agent sandbox" not in w


# ---------------------------------------------------------------------------
# 8. Structural guard — the observability call precedes import/AgentContext
# ---------------------------------------------------------------------------

def test_observability_call_precedes_import_in_source():
    import agentnode_sdk
    src = (Path(agentnode_sdk.__file__).parent / "runtimes" / "agent_runner.py").read_text(encoding="utf-8")
    emit = src.index("_emit_host_execution_observability(\n")
    ctx = src.index("context = AgentContext(")
    load = src.index("func = _load_agent_entrypoint(")
    assert emit < ctx < load
