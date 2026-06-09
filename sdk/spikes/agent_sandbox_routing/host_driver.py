"""Host-side driver for the agent-sandbox routing spike (throwaway).

Spawns the wrapper either in a hardened container (``backend="container"``) or,
for offline protocol verification, as a plain subprocess (``backend="local"`` —
INSECURE, no isolation). It then runs the single-threaded request/response loop:

  * ``run_tool`` requests are decided HOST-side (allowlist + tool-call limit) and
    routed to the REAL ``agentnode_sdk.runner.run_tool`` (the gated/sandboxed
    path). The container's self-reported limits are ignored.
  * ``call_llm`` requests are answered by the fake host-side broker — the
    container never sees an API key.

Nothing here is production code or wired into ``run_agent``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid

from . import fake_llm
from .container_agent_wrapper import WRAPPER_SOURCE


class SpikeHost:
    def __init__(self, *, allowed_packages, max_tool_calls):
        self.allowed = set(allowed_packages or [])
        self.max_tool_calls = max_tool_calls
        self.tool_calls = 0
        self.events: list = []          # audit of what the host serviced
        self.rpc_latencies: list = []   # seconds per serviced request

    def handle(self, req: dict) -> dict:
        t0 = time.monotonic()
        resp = self._handle(req)
        self.rpc_latencies.append(time.monotonic() - t0)
        return resp

    def _handle(self, req: dict) -> dict:
        rid = req.get("id")
        rtype = req.get("type")
        if rtype == "run_tool":
            slug = req.get("slug")
            # HOST-side policy. The container cannot widen its own allowlist or
            # exceed the limit by lying — the host owns the decision.
            if slug not in self.allowed:
                self.events.append(("refused_allowlist", slug))
                return {"id": rid, "ok": False, "error": f"tool '{slug}' not in agent allowlist"}
            self.tool_calls += 1
            if self.tool_calls > self.max_tool_calls:
                self.events.append(("refused_limit", slug))
                return {"id": rid, "ok": False, "error": "tool-call limit exceeded"}
            # The REAL gated runner. For an uninstalled spike slug this returns a
            # clean not-installed failure — fine; the spike proves the host routes
            # here (real trust/sandbox/guard pipeline), not that the tool executes.
            from agentnode_sdk.runner import run_tool
            r = run_tool(slug, req.get("tool_name"), **(req.get("kwargs") or {}))
            self.events.append(("run_tool", slug, r.mode_used))
            return {"id": rid, "ok": True, "result": {
                "success": r.success, "result": r.result,
                "error": r.error, "mode_used": r.mode_used,
            }}
        if rtype == "call_llm":
            self.events.append(("call_llm",))
            return {"id": rid, "ok": True, "completion": fake_llm.complete(req.get("messages") or [])}
        return {"id": rid, "ok": False, "error": f"unknown request type: {rtype}"}


def _readline_timeout(stream, timeout: float) -> str:
    box: list = [None]
    t = threading.Thread(target=lambda: box.__setitem__(0, stream.readline()), daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"no control message within {timeout}s")
    return box[0]


def _build_argv(backend: str, name: str):
    """Return (argv, container_name_or_None, runtime_or_None)."""
    if backend == "local":
        # INSECURE: no isolation. Protocol verification only.
        return [sys.executable, "-c", WRAPPER_SOURCE], None, None
    # Container path — use the REAL ContainerBackend (its hardened flags), not the
    # default backend (test suites fake that to force availability). Fail-closed
    # if no runtime/image is actually present.
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.sandbox.types import ProcessSpec
    be = ContainerBackend()
    avail = be.check_available()
    if not avail.available:
        raise RuntimeError(f"no usable container runtime/image: {avail.reason}")
    spec = ProcessSpec(
        command=["python3", "-c", WRAPPER_SOURCE],
        network="none", env={}, mounts=[], clean_home=True,
        interactive=True, name=name,
    )
    argv = be.wrap_command(spec)
    return argv, name, avail.backend


def run_spike(agent_source: str, *, goal: str = "", kwargs: dict | None = None,
              allowed_packages=(), max_tool_calls: int = 5,
              backend: str = "container", timeout: float = 30.0) -> dict:
    host = SpikeHost(allowed_packages=allowed_packages, max_tool_calls=max_tool_calls)
    name = f"agentnode-agent-spike-{uuid.uuid4().hex[:8]}"
    argv, container_name, runtime = _build_argv(backend, name)
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    t_start = time.monotonic()
    try:
        init = {"agent_source": agent_source, "function": "run", "goal": goal, "kwargs": kwargs or {}}
        proc.stdin.write(json.dumps(init) + "\n")
        proc.stdin.flush()
        while True:
            line = _readline_timeout(proc.stdout, timeout)
            if not line:
                err = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError("container closed control channel early; stderr=" + (err or ""))
            msg = json.loads(line)
            if msg.get("type") == "result":
                return {
                    "result": msg,
                    "events": host.events,
                    "rpc_latencies": host.rpc_latencies,
                    "total_seconds": time.monotonic() - t_start,
                }
            resp = host.handle(msg)
            proc.stdin.write(json.dumps(resp) + "\n")
            proc.stdin.flush()
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        if container_name and runtime:
            try:
                subprocess.run([runtime, "rm", "-f", container_name], capture_output=True, timeout=10)
            except Exception:
                pass
