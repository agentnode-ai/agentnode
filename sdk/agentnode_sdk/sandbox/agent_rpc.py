"""Agent RPC protocol + host-side loop (Sprint A — interfaces only, un-wired).

The host side of the future sandboxed-agent loop: it services an agent's requests
over an :class:`~agentnode_sdk.sandbox.agent_session.AgentSandboxSession`,
enforcing the allowlist + tool-call limit HOST-side and routing ``run_tool`` to
the real gated runner and ``call_llm`` to a credential broker.

NOTHING in production constructs or calls this yet. ``run_agent`` is unchanged.
Tool/LLM execution is injected so the host is testable offline; the default
tool runner is the real ``runner.run_tool`` (lazy-imported, never invoked in
Sprint A). See ``docs/design/agent-sandbox-architecture.md``.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from agentnode_sdk.sandbox.agent_session import AgentSandboxSession

PROTOCOL_VERSION = 1


class AgentRpcError(RuntimeError):
    """Protocol/transport-level error on the host side."""


# ---------------------------------------------------------------------------
# Wire codec (line-framed JSON) — the production transports are line-oriented.
# ---------------------------------------------------------------------------

def encode(message: dict) -> str:
    """Encode one protocol message as a single newline-terminated JSON line."""
    return json.dumps(message) + "\n"


def decode(line: str) -> dict:
    """Decode one protocol line into a dict (rejects non-objects)."""
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise AgentRpcError("protocol message must be a JSON object")
    return obj


def _response(rid: Any, *, ok: bool, **fields: Any) -> dict:
    return {"id": rid, "type": "response", "ok": ok, **fields}


# ---------------------------------------------------------------------------
# Target trust policy (PURE; adopted by run_agent only in Sprint B)
# ---------------------------------------------------------------------------

def agent_execution_mode(trust_level: str | None) -> str:
    """Target execution mode for an agent of the given trust level.

    ``"host"`` (curated), ``"sandbox"`` (trusted/verified/unverified), or
    ``"refused"`` (unknown/missing). This is the FUTURE policy — it is NOT what
    ``run_agent`` does today (today: trusted/curated host, community refused).
    """
    t = (trust_level or "").lower()
    if t == "curated":
        return "host"
    if t in ("trusted", "verified", "unverified"):
        return "sandbox"
    return "refused"


# ---------------------------------------------------------------------------
# Default tool runner — the REAL gated path (lazy import; never called in A)
# ---------------------------------------------------------------------------

def _default_tool_runner(slug: str, tool_name: str | None, kwargs: dict) -> dict:
    from agentnode_sdk.runner import run_tool
    r = run_tool(slug, tool_name, **kwargs)
    return {
        "success": r.success, "result": r.result,
        "error": r.error, "mode_used": r.mode_used,
    }


# ---------------------------------------------------------------------------
# Host loop
# ---------------------------------------------------------------------------

ToolRunner = Callable[[str, "str | None", dict], dict]
LlmBroker = Callable[[list], dict]


class AgentRpcHost:
    """Services a sandboxed agent's requests over an ``AgentSandboxSession``.

    The host owns the decision: the agent (in the sandbox) can neither widen its
    allowlist nor exceed its tool-call limit nor see secrets — it only requests.

    Args:
        allowed_packages: slugs the agent may call (host-enforced allowlist).
        max_tool_calls: host-enforced tool-call limit.
        tool_runner: ``(slug, tool_name, kwargs) -> result_dict``. Defaults to the
            real gated ``runner.run_tool``. Inject a fake in tests.
        llm_broker: ``(messages) -> completion_dict`` (the credential broker).
            ``None`` → ``call_llm`` is refused (no broker in Sprint A).
    """

    def __init__(self, *, allowed_packages, max_tool_calls: int,
                 tool_runner: ToolRunner | None = None,
                 llm_broker: LlmBroker | None = None):
        self.allowed = set(allowed_packages or [])
        self.max_tool_calls = max_tool_calls
        self.tool_calls = 0
        self.events: list = []
        self._tool_runner = tool_runner or _default_tool_runner
        self._llm_broker = llm_broker

    def handle(self, req: dict) -> dict:
        rid = req.get("id")
        rtype = req.get("type")
        if rtype == "run_tool":
            slug = req.get("slug")
            if slug not in self.allowed:
                self.events.append(("refused_allowlist", slug))
                return _response(rid, ok=False, error=f"tool '{slug}' not in agent allowlist")
            self.tool_calls += 1
            if self.tool_calls > self.max_tool_calls:
                self.events.append(("refused_limit", slug))
                return _response(rid, ok=False, error="tool-call limit exceeded")
            result = self._tool_runner(slug, req.get("tool_name"), req.get("kwargs") or {})
            self.events.append(("run_tool", slug))
            return _response(rid, ok=True, result=result)
        if rtype == "call_llm":
            if self._llm_broker is None:
                return _response(rid, ok=False, error="no LLM broker configured")
            self.events.append(("call_llm",))
            return _response(rid, ok=True, completion=self._llm_broker(req.get("messages") or []))
        return _response(rid, ok=False, error=f"unknown request type: {rtype!r}")

    def run(self, session: AgentSandboxSession, *, init: dict,
            timeout: float = 30.0) -> dict:
        """Drive one agent run over ``session`` until it emits a ``result``."""
        session.send({"type": "init", "version": PROTOCOL_VERSION, **init})
        while True:
            msg = session.recv(timeout)
            if msg is None:
                raise AgentRpcError("session closed before the agent returned a result")
            if msg.get("type") == "result":
                return {"result": msg, "events": self.events}
            session.send(self.handle(msg))
