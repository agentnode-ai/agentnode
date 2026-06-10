"""B2a — run_agent routing behind the default-OFF agent-sandbox flag.

All Docker-free: a fake backend returns a scripted FakeSession, and the volume
``inspect`` is mocked. These prove the flag default-OFF leaves behaviour
unchanged, that community agents route sandbox-or-fail-closed (never host) when
the flag is ON, and that trusted/curated/unknown are unchanged.
"""
from __future__ import annotations

import json
import types
from unittest import mock

import pytest

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.runtimes.agent_runner import run_agent
from agentnode_sdk.runtimes.agent_sandbox import _agent_sandbox_enabled, run_agent_sandboxed
from agentnode_sdk.sandbox import sandbox_volume_name
from agentnode_sdk.sandbox.agent_session import FakeSession
from agentnode_sdk.sandbox.types import SandboxAvailability


def _agent_entry(**overrides):
    """Minimal agent lockfile entry that reaches run_agent's trust gate."""
    base = {
        "version": "1.0.0",
        "package_type": "agent",
        "runtime": "python",
        "entrypoint": "",
        "trust_level": "trusted",
        "agent": {
            "entrypoint": "test_module:agent_func",
            "goal": "Test goal",
            "tool_access": {"allowed_packages": ["csv-analyzer-pack"]},
            "limits": {"max_iterations": 5, "max_tool_calls": 10, "max_runtime_seconds": 30},
            "termination": {"stop_on_final_answer": True, "stop_on_consecutive_errors": 3},
        },
    }
    base.update(overrides)
    return base


class _FakeBackend:
    def __init__(self, available=True, session=None):
        self._available = available
        self._session = session
        self.opened_spec = None

    def check_available(self):
        return SandboxAvailability(
            available=self._available,
            backend="docker" if self._available else "none",
            reason="" if self._available else "no container runtime",
        )

    def open_agent_session(self, spec):
        self.opened_spec = spec
        return self._session


def _sandboxed_entry(slug="comm-agent", trust="unverified",
                     entrypoint="comm_agent:run", allowed=None, llm_access=None):
    version, ahash = "1.0.0", "sha256:abc123"
    vol = sandbox_volume_name(slug, version, ahash)
    agent_cfg = {
        "entrypoint": entrypoint, "goal": "g",
        "limits": {"max_tool_calls": 5, "max_runtime_seconds": 30},
        "tool_access": {"allowed_packages": allowed},
    }
    if llm_access is not None:
        agent_cfg["llm_access"] = llm_access
    entry = {
        "version": version, "artifact_hash": ahash, "trust_level": trust,
        "package_type": "agent", "sandboxed": True, "sandbox_volume": vol,
        "agent": agent_cfg,
    }
    return entry, agent_cfg


def _mock_volume_inspect_ok(monkeypatch):
    monkeypatch.setattr(
        "agentnode_sdk.runtimes.agent_sandbox.subprocess.run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFlag:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("AGENTNODE_AGENT_SANDBOX", raising=False)
        monkeypatch.setattr("agentnode_sdk.config.load_config", lambda: {})
        assert _agent_sandbox_enabled() is False

    def test_env_on(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "1")
        assert _agent_sandbox_enabled() is True

    def test_config_on(self, monkeypatch):
        monkeypatch.delenv("AGENTNODE_AGENT_SANDBOX", raising=False)
        monkeypatch.setattr("agentnode_sdk.config.load_config",
                            lambda: {"agent_sandbox": {"enabled": True}})
        assert _agent_sandbox_enabled() is True


# ---------------------------------------------------------------------------
# run_agent routing (flag OFF = unchanged; flag ON = community → sandbox)
# ---------------------------------------------------------------------------

class TestRouting:
    def test_flag_off_community_still_refused(self, monkeypatch):
        monkeypatch.delenv("AGENTNODE_AGENT_SANDBOX", raising=False)
        monkeypatch.setattr("agentnode_sdk.config.load_config", lambda: {})
        result = run_agent("test-agent", entry=_agent_entry(trust_level="unverified"))
        assert result.success is False
        assert "trust level" in (result.error or "")

    def test_flag_on_routes_community_to_sandbox(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "1")
        seen = {}

        def spy(slug, entry, agent_config, *, goal=None, run_id=None, **kwargs):
            seen["slug"] = slug
            return RunToolResult(success=False, error="spy", mode_used="agent_sandbox", run_id=run_id)

        monkeypatch.setattr("agentnode_sdk.runtimes.agent_sandbox.run_agent_sandboxed", spy)
        result = run_agent("test-agent", entry=_agent_entry(trust_level="verified"))
        assert seen.get("slug") == "test-agent"
        assert result.mode_used == "agent_sandbox"

    def test_flag_on_trusted_stays_host(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "1")
        result = run_agent("test-agent", entry=_agent_entry(trust_level="trusted"))
        # trusted not in {verified,unverified} → branch skipped → host path
        assert "trust level" not in (result.error or "")
        assert result.mode_used != "sandbox_unavailable"

    def test_flag_on_unknown_still_refused(self, monkeypatch):
        monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "1")
        result = run_agent("test-agent", entry=_agent_entry(trust_level="unknown"))
        assert result.success is False
        assert "trust level" in (result.error or "")


# ---------------------------------------------------------------------------
# run_agent_sandboxed internals (fail-closed, volume gate, session, no broker)
# ---------------------------------------------------------------------------

class TestRunAgentSandboxed:
    def test_volume_gate_fail_closed(self):
        entry = {"version": "1.0.0", "artifact_hash": "sha256:x",
                 "trust_level": "unverified", "sandboxed": False}
        r = run_agent_sandboxed("comm", entry, {"entrypoint": "m:run"})
        assert r.success is False
        assert r.mode_used == "sandbox_unavailable"
        assert "reinstall" in r.error.lower()

    def test_backend_unavailable_fail_closed(self, monkeypatch):
        entry, cfg = _sandboxed_entry()
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend",
                            lambda: _FakeBackend(available=False))
        r = run_agent_sandboxed("comm-agent", entry, cfg)
        assert r.success is False and r.mode_used == "sandbox_unavailable"
        assert "refusing to run community agent code on the host" in r.error

    def test_happy_path_uses_session_and_closes(self, monkeypatch):
        entry, cfg = _sandboxed_entry(allowed=["allowed-pack"])
        session = FakeSession(agent_script=[{"id": 0, "type": "result", "ok": True, "value": {"done": True}}])
        backend = _FakeBackend(available=True, session=session)
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend", lambda: backend)
        _mock_volume_inspect_ok(monkeypatch)
        r = run_agent_sandboxed("comm-agent", entry, cfg)
        assert r.success is True and r.result == {"done": True}
        assert r.mode_used == "agent_sandbox"
        assert backend.opened_spec is not None          # open_agent_session used
        assert session.closed is True                    # closed in finally
        # spec hardening
        spec = backend.opened_spec
        assert spec.network == "none"
        assert spec.env == {"PYTHONPATH": "/pack"}
        assert any(m.dst == "/pack" and m.read_only for m in spec.mounts)

    def test_session_closed_on_error(self, monkeypatch):
        entry, cfg = _sandboxed_entry()

        class _BoomSession(FakeSession):
            def recv(self, timeout):
                raise RuntimeError("boom")

        session = _BoomSession()
        backend = _FakeBackend(available=True, session=session)
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend", lambda: backend)
        _mock_volume_inspect_ok(monkeypatch)
        r = run_agent_sandboxed("comm-agent", entry, cfg)
        assert r.success is False
        assert session.closed is True   # finally ran despite the error

    def test_default_deny_call_llm_graceful_run_continues(self, monkeypatch):
        """C1: no llm_access → call_llm is refused host-side as a GRACEFUL per-call
        error (ok=False); the run stays alive and reaches its result — no whole-run
        crash, no host fallback."""
        entry, cfg = _sandboxed_entry()  # no llm_access → default-deny
        session = FakeSession(agent_script=[
            {"id": 1, "type": "call_llm", "messages": [{"role": "user", "content": "hi"}]},
            {"id": 0, "type": "result", "ok": True, "value": {"done": True}},
        ])
        backend = _FakeBackend(available=True, session=session)
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend", lambda: backend)
        _mock_volume_inspect_ok(monkeypatch)
        r = run_agent_sandboxed("comm-agent", entry, cfg)
        # the call_llm got a graceful refusal...
        refusals = [m for m in session.sent
                    if m.get("type") == "response" and m.get("ok") is False]
        assert any("not granted" in (m.get("error") or "") for m in refusals)
        # ...and the run continued to its result (stayed alive, never host)
        assert r.success is True and r.result == {"done": True}
        assert r.mode_used == "agent_sandbox"
        assert session.closed is True

    def test_call_llm_succeeds_with_broker(self, monkeypatch):
        """C1: with llm_access.enabled, call_llm is serviced HOST-side by the
        policy broker; the completion (NOT the provider key) is returned to the
        agent, and no key crosses into the container env."""
        entry, cfg = _sandboxed_entry(llm_access={"enabled": True})
        session = FakeSession(agent_script=[
            {"id": 1, "type": "call_llm", "messages": [{"role": "user", "content": "hi"}]},
            {"id": 0, "type": "result", "ok": True, "value": {"done": True}},
        ])
        backend = _FakeBackend(available=True, session=session)
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend", lambda: backend)
        _mock_volume_inspect_ok(monkeypatch)

        class _Msg:
            content = "completion text"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        class _Cmp:
            @staticmethod
            def create(**kw):
                return _Resp()

        class _Chat:
            completions = _Cmp()

        class _Client:
            chat = _Chat()

        monkeypatch.setattr("agentnode_sdk.runtimes.agent_runner._auto_detect_llm",
                            lambda *a, **k: {"client": _Client(), "provider": "openai", "model": "x"})
        run_agent_sandboxed("comm-agent", entry, cfg)
        responses = [m for m in session.sent if m.get("type") == "response" and m.get("ok")]
        assert any((m.get("completion") or {}).get("content") == "completion text" for m in responses)
        # no provider key crossed into the container env
        assert "OPENAI_API_KEY" not in (backend.opened_spec.env or {})
        assert "ANTHROPIC_API_KEY" not in (backend.opened_spec.env or {})

    def test_open_session_failure_fail_closed(self, monkeypatch):
        """R1: a sandbox-START failure returns a clean sandbox_unavailable —
        never raises, never host."""
        entry, cfg = _sandboxed_entry()

        class _RaisingBackend(_FakeBackend):
            def open_agent_session(self, spec):
                raise OSError("container runtime vanished")

        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend",
                            lambda: _RaisingBackend(available=True))
        _mock_volume_inspect_ok(monkeypatch)
        r = run_agent_sandboxed("comm-agent", entry, cfg)
        assert r.success is False
        assert r.mode_used == "sandbox_unavailable"


class TestSandboxAudit:
    """C2: ONE aggregated, sanitized audit record per sandboxed run — counters,
    caps, and fixed reason codes only; never prompts/keys/raw provider errors."""

    @staticmethod
    def _records(audit_dir):
        f = audit_dir / "audit.jsonl"
        if not f.exists():
            return []
        return [json.loads(line) for line in
                f.read_text(encoding="utf-8").strip().splitlines() if line]

    def _run_with_audit(self, monkeypatch, tmp_path, entry, cfg, session, run_id="rid-1"):
        audit_dir = tmp_path / ".agentnode"
        backend = _FakeBackend(available=True, session=session)
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend", lambda: backend)
        _mock_volume_inspect_ok(monkeypatch)
        with mock.patch("agentnode_sdk.config.config_dir", return_value=audit_dir):
            r = run_agent_sandboxed("comm-agent", entry, cfg, run_id=run_id)
        return r, self._records(audit_dir)

    def test_one_aggregated_record_per_run(self, monkeypatch, tmp_path):
        entry, cfg = _sandboxed_entry()  # no llm_access → default-deny
        session = FakeSession(agent_script=[
            {"id": 1, "type": "call_llm", "messages": [{"role": "user", "content": "hi"}]},
            {"id": 2, "type": "run_tool", "slug": "evil-pack", "tool_name": None, "kwargs": {}},
            {"id": 0, "type": "result", "ok": True, "value": {"done": True}},
        ])
        r, records = self._run_with_audit(monkeypatch, tmp_path, entry, cfg, session)
        assert r.success is True
        assert len(records) == 1                       # aggregated, not per-call
        rec = records[0]
        assert rec["event"] == "agent_run"
        assert rec["slug"] == "comm-agent"
        assert rec["action"] == "allow"
        assert rec["reason"] == "agent_completed"
        assert rec["source"] == "agent_sandbox"
        assert rec["trust"] == "unverified"
        assert rec["request_id"] == "rid-1"
        assert rec["sandbox"] is True
        assert rec["llm"]["enabled"] is False
        assert rec["llm"]["refused_disabled"] == 1
        assert rec["tool_calls"] == 0                  # evil-pack never ran
        assert rec["tool_refusals"] == 1               # host-side allowlist refusal

    def test_audit_no_prompt_secret_or_agent_error_leak(self, monkeypatch, tmp_path):
        entry, cfg = _sandboxed_entry(llm_access={"enabled": True})
        sentinel_prompt = "SENTINEL-PROMPT-DO-NOT-AUDIT"
        secret = "sk-AUDIT-LEAK-TEST"
        agent_error = "agent-authored error text"
        session = FakeSession(agent_script=[
            {"id": 1, "type": "call_llm", "messages": [{"role": "user", "content": sentinel_prompt}]},
            {"id": 0, "type": "result", "ok": False, "error": agent_error},
        ])

        def boom(*a, **k):
            raise RuntimeError(f"401 key={secret} url=https://internal/v1")

        monkeypatch.setattr("agentnode_sdk.runtimes.agent_runner._auto_detect_llm", boom)
        r, records = self._run_with_audit(monkeypatch, tmp_path, entry, cfg, session)
        assert r.success is False
        assert len(records) == 1
        text = json.dumps(records)
        assert sentinel_prompt not in text             # no prompt content
        assert secret not in text                      # no key / raw provider error
        assert agent_error not in text                 # no agent-authored text
        assert records[0]["reason"] == "agent_error"   # fixed code instead
        assert records[0]["llm"]["provider_errors"] == 1

    def test_fail_closed_gate_audited(self, monkeypatch, tmp_path):
        entry, cfg = _sandboxed_entry()
        audit_dir = tmp_path / ".agentnode"
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend",
                            lambda: _FakeBackend(available=False))
        with mock.patch("agentnode_sdk.config.config_dir", return_value=audit_dir):
            r = run_agent_sandboxed("comm-agent", entry, cfg, run_id="rid-2")
        assert r.success is False
        recs = self._records(audit_dir)
        assert len(recs) == 1
        assert recs[0]["action"] == "deny"
        assert recs[0]["reason"] == "backend_unavailable"
        assert recs[0]["llm"] is None                  # LLM never engaged

    def test_disallowed_model_graceful_and_audited(self, monkeypatch, tmp_path):
        entry, cfg = _sandboxed_entry(
            llm_access={"enabled": True, "allowed_models": ["allowed-model"]})
        session = FakeSession(agent_script=[
            {"id": 1, "type": "call_llm", "messages": [{"role": "user", "content": "hi"}]},
            {"id": 0, "type": "result", "ok": True, "value": {"done": True}},
        ])
        created = {"n": 0}

        class _Cmp:
            @staticmethod
            def create(**kw):
                created["n"] += 1
                raise AssertionError("provider must not be called")

        class _Chat:
            completions = _Cmp()

        class _Client:
            chat = _Chat()

        monkeypatch.setattr(
            "agentnode_sdk.runtimes.agent_runner._auto_detect_llm",
            lambda *a, **k: {"client": _Client(), "provider": "openai", "model": "host-model-x"})
        r, records = self._run_with_audit(monkeypatch, tmp_path, entry, cfg, session)
        # graceful per-call refusal — run completed, no host model name to the agent
        refusals = [m for m in session.sent
                    if m.get("type") == "response" and m.get("ok") is False]
        assert any("not allowed" in (m.get("error") or "") for m in refusals)
        assert all("host-model-x" not in (m.get("error") or "") for m in refusals)
        assert r.success is True
        assert created["n"] == 0                       # provider never called
        rec = records[0]
        assert rec["llm"]["refused_model"] == 1
        assert rec["llm"]["model"] == "host-model-x"   # host-side audit only
        assert rec["llm"]["allowed_models"] == ["allowed-model"]

    def test_audit_failure_never_breaks_run(self, monkeypatch, tmp_path):
        entry, cfg = _sandboxed_entry()
        session = FakeSession(agent_script=[
            {"id": 0, "type": "result", "ok": True, "value": {"done": True}}])
        backend = _FakeBackend(available=True, session=session)
        monkeypatch.setattr("agentnode_sdk.sandbox.get_default_backend", lambda: backend)
        _mock_volume_inspect_ok(monkeypatch)
        with mock.patch("agentnode_sdk.config.config_dir", side_effect=OSError("disk full")):
            r = run_agent_sandboxed("comm-agent", entry, cfg)
        assert r.success is True and r.result == {"done": True}


def test_no_host_exec_in_sandbox_path():
    """The sandbox path must never reach the host execution primitives."""
    import agentnode_sdk
    from pathlib import Path
    src = (Path(agentnode_sdk.__file__).parent / "runtimes" / "agent_sandbox.py").read_text(encoding="utf-8")
    for tok in ("_execute_with_timeout", "_execute_with_process", "pip_install"):
        assert tok not in src
