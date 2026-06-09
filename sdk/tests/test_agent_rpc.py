"""Sprint A unit tests for the agent-sandbox RPC core (interfaces only, un-wired).

These are pure / in-process (FakeSession) — no Docker, no run_agent — so they run
in the normal suite. They also assert the new modules are imported by NO
production path (the no-importer guard), which is what keeps Sprint A behaviour-
neutral.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentnode_sdk.sandbox.agent_session import AgentSandboxSession, FakeSession
from agentnode_sdk.sandbox.agent_rpc import (
    PROTOCOL_VERSION,
    AgentRpcError,
    AgentRpcHost,
    agent_execution_mode,
    decode,
    encode,
)


# ---------------------------------------------------------------------------
# Wire codec
# ---------------------------------------------------------------------------

class TestProtocolCodec:
    def test_round_trip(self):
        msg = {"id": 1, "type": "run_tool", "slug": "p", "kwargs": {"x": 1}}
        assert decode(encode(msg).strip()) == msg

    def test_encode_is_newline_framed(self):
        assert encode({"a": 1}).endswith("\n")

    def test_decode_rejects_non_object(self):
        with pytest.raises(AgentRpcError):
            decode("[1, 2, 3]")

    def test_version_constant(self):
        assert isinstance(PROTOCOL_VERSION, int) and PROTOCOL_VERSION >= 1


# ---------------------------------------------------------------------------
# Target trust policy (pure; not yet applied by run_agent)
# ---------------------------------------------------------------------------

class TestAgentExecutionMode:
    @pytest.mark.parametrize("trust,expected", [
        ("curated", "host"),
        ("trusted", "sandbox"),
        ("verified", "sandbox"),
        ("unverified", "sandbox"),
        (None, "refused"),
        ("", "refused"),
        ("unknown", "refused"),
        ("preview", "refused"),
    ])
    def test_mode(self, trust, expected):
        assert agent_execution_mode(trust) == expected


# ---------------------------------------------------------------------------
# Host request handling (host owns allowlist + limit + broker)
# ---------------------------------------------------------------------------

def _host(**over):
    kw = dict(
        allowed_packages=["allowed-pack"], max_tool_calls=2,
        tool_runner=lambda slug, tool, kw: {"ran": slug, "tool": tool, "kw": kw},
        llm_broker=lambda messages: {"role": "assistant", "content": "[broker] ok"},
    )
    kw.update(over)
    return AgentRpcHost(**kw)


class TestAgentRpcHostHandle:
    def test_run_tool_allowed_routes_to_runner(self):
        h = _host()
        resp = h.handle({"id": 1, "type": "run_tool", "slug": "allowed-pack", "kwargs": {"a": 1}})
        assert resp["ok"] is True
        assert resp["result"]["ran"] == "allowed-pack"
        assert ("run_tool", "allowed-pack") in h.events

    def test_run_tool_not_allowlisted_refused_hostside(self):
        h = _host()
        resp = h.handle({"id": 2, "type": "run_tool", "slug": "evil-pack"})
        assert resp["ok"] is False
        assert "allowlist" in resp["error"]
        assert ("refused_allowlist", "evil-pack") in h.events

    def test_tool_call_limit_enforced_hostside(self):
        h = _host(max_tool_calls=1)
        ok = h.handle({"id": 1, "type": "run_tool", "slug": "allowed-pack"})
        over = h.handle({"id": 2, "type": "run_tool", "slug": "allowed-pack"})
        assert ok["ok"] is True
        assert over["ok"] is False and "limit" in over["error"]

    def test_call_llm_uses_broker(self):
        h = _host()
        resp = h.handle({"id": 3, "type": "call_llm", "messages": [{"role": "user", "content": "hi"}]})
        assert resp["ok"] is True
        assert resp["completion"]["content"].startswith("[broker]")

    def test_call_llm_refused_without_broker(self):
        h = _host(llm_broker=None)
        resp = h.handle({"id": 4, "type": "call_llm", "messages": []})
        assert resp["ok"] is False and "broker" in resp["error"]

    def test_unknown_request_type_refused(self):
        h = _host()
        resp = h.handle({"id": 5, "type": "exfiltrate"})
        assert resp["ok"] is False and "unknown" in resp["error"]


# ---------------------------------------------------------------------------
# Host loop over a FakeSession (no container)
# ---------------------------------------------------------------------------

class TestAgentRpcHostRun:
    def test_full_run_services_tool_and_llm(self):
        script = [
            {"id": 1, "type": "run_tool", "slug": "allowed-pack", "kwargs": {}},
            {"id": 2, "type": "call_llm", "messages": [{"role": "user", "content": "ping"}]},
            {"id": 0, "type": "result", "ok": True, "value": {"done": True}},
        ]
        session = FakeSession(agent_script=script)
        h = _host()
        out = h.run(session, init={"agent": "x", "goal": "spike"}, timeout=5)
        assert out["result"]["value"] == {"done": True}
        # host sent: init first, then a response per request
        assert session.sent[0]["type"] == "init"
        assert session.sent[0]["version"] == PROTOCOL_VERSION
        assert [e[0] for e in h.events] == ["run_tool", "call_llm"]
        assert isinstance(session, AgentSandboxSession)

    def test_timeout_when_agent_silent(self):
        session = FakeSession(agent_script=[])  # nothing ever arrives
        h = _host()
        with pytest.raises(TimeoutError):
            h.run(session, init={}, timeout=0.1)


# ---------------------------------------------------------------------------
# Behaviour-neutrality guard: nothing in production imports the new modules
# ---------------------------------------------------------------------------

def test_no_production_importer_of_agent_rpc_modules():
    """B2a update: ``agent_rpc`` (the host loop) is now legitimately wired into the
    agent run path by ``agent_sandbox.py`` (the B2a routing); it must NOT appear
    anywhere else. ``agent_session`` stays confined to the sandbox backend. (B1
    added agent_session to the backend; B2a adds agent_rpc to agent_sandbox.)"""
    import agentnode_sdk
    root = Path(agentnode_sdk.__file__).parent
    rpc_pat = re.compile(r"\bagent_rpc\b")
    sess_pat = re.compile(r"\bagent_session\b")
    # files where each name may legitimately appear
    rpc_allowed = {"agent_rpc.py", "agent_sandbox.py"}
    sess_allowed = {"agent_session.py", "agent_rpc.py", "backend.py", "container_backend.py"}
    rpc_offenders, sess_offenders = [], []
    for p in root.rglob("*.py"):
        txt = p.read_text(encoding="utf-8")
        if p.name not in rpc_allowed and rpc_pat.search(txt):
            rpc_offenders.append(str(p.relative_to(root)))
        if p.name not in sess_allowed and sess_pat.search(txt):
            sess_offenders.append(str(p.relative_to(root)))
    assert rpc_offenders == [], f"agent_rpc imported by production (B2 not done): {rpc_offenders}"
    assert sess_offenders == [], f"agent_session leaked outside the sandbox backend: {sess_offenders}"
