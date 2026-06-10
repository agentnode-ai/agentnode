"""B2a: route community (verified/unverified) agents through a sandboxed session,
behind a default-OFF feature flag.

Policy (only when the flag is ON):
  verified / unverified  -> sandbox-or-fail-closed (this module); NEVER host.
  trusted / curated      -> host (handled in run_agent, unchanged).
  unknown / missing       -> refused (run_agent's existing gate).

This is the FIRST behaviour change of the agent-sandbox bow. With the flag OFF
(default) nothing here runs and run_agent behaves exactly as before. No host
fallback: if no backend/volume is available, fail-closed. Tool-calls go host-side
through the real ``runner.run_tool`` (via AgentRpcHost); LLM has no broker yet
(B2b) so ``call_llm`` fails cleanly. See docs/design/agent-sandbox-architecture.md.
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid


def _agent_sandbox_enabled() -> bool:
    """Default OFF. Env ``AGENTNODE_AGENT_SANDBOX`` wins, else config
    ``agent_sandbox.enabled`` in ~/.agentnode/config.json."""
    if os.environ.get("AGENTNODE_AGENT_SANDBOX", "").strip().lower() in ("1", "true"):
        return True
    try:
        from agentnode_sdk.config import load_config
        section = (load_config() or {}).get("agent_sandbox") or {}
        return bool(section.get("enabled", False))
    except Exception:
        return False


def run_agent_sandboxed(slug, entry, agent_config, *, goal=None, run_id=None, **kwargs):
    """Run a community agent's entrypoint inside the sandbox. Returns a
    RunToolResult. Fail-closed on a missing/stale volume or no runtime — never
    runs the agent on the host."""
    from agentnode_sdk.models import RunToolResult
    from agentnode_sdk.sandbox import get_default_backend, sandbox_volume_name
    from agentnode_sdk.sandbox.agent_container_wrapper import WRAPPER_SOURCE
    from agentnode_sdk.sandbox.agent_rpc import AgentRpcHost
    from agentnode_sdk.sandbox.types import MountSpec, ProcessSpec

    def _fail(error: str, mode: str = "sandbox_unavailable") -> "RunToolResult":
        return RunToolResult(success=False, error=error, mode_used=mode, run_id=run_id)

    agent_config = agent_config or {}
    entrypoint = agent_config.get("entrypoint", "")
    if not entrypoint:
        return _fail(f"Agent '{slug}' has no entrypoint defined.", mode="agent_sandbox")

    limits = agent_config.get("limits") or {}
    max_tool_calls = limits.get("max_tool_calls", 40)
    timeout = float(limits.get("max_runtime_seconds", 180))
    allowed = (agent_config.get("tool_access") or {}).get("allowed_packages")
    effective_goal = goal or agent_config.get("goal", "")

    # Volume gate — mirror python_runner._run_container; never trust the lockfile
    # blindly. The community agent's code was built into this volume at install.
    expected_vol = sandbox_volume_name(slug, entry.get("version"), entry.get("artifact_hash"))
    if not entry.get("sandboxed") or entry.get("sandbox_volume") != expected_vol:
        return _fail(
            f"Agent sandbox volume missing or stale — reinstall '{slug}' "
            f"(run: agentnode install {slug})."
        )

    backend = get_default_backend()
    avail = backend.check_available()
    if not avail.available:
        return _fail(
            "Agent execution requires a container runtime + the pinned image. "
            f"{avail.reason or 'none available'} — refusing to run community agent "
            "code on the host."
        )

    runtime = avail.backend or "docker"
    try:
        insp = subprocess.run(
            [runtime, "volume", "inspect", expected_vol],
            capture_output=True, timeout=10,
        )
    except Exception as exc:
        return _fail(f"Could not verify sandbox volume: {exc}")
    if insp.returncode != 0:
        return _fail(f"Agent sandbox volume missing — reinstall '{slug}'.")

    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", slug)[:40] or "agent"
    spec = ProcessSpec(
        command=["python", "-c", WRAPPER_SOURCE],
        network="none",
        env={"PYTHONPATH": "/pack"},
        mounts=[MountSpec(src=expected_vol, dst="/pack", read_only=True)],
        clean_home=True,
        interactive=True,
        name=f"agentnode-agent-{safe}-{uuid.uuid4().hex[:8]}",
    )

    # B2a: STRICT host-side allowlist (the agent's declared tool_access). A
    # community agent with no declared allowlist gets no tool access — broadening
    # "unrestricted" community agents is a later decision.
    # B2b-1: LLM calls go through the host-side broker (the provider key stays on
    # the host, never in the container).
    # C1: that broker is wrapped by a default-DENY credential policy — the agent
    # reaches the host LLM key only if it declared llm_access.enabled, and the
    # host-config ceiling (agent_sandbox.llm) always wins. Refusals come back as
    # graceful per-call errors (the agent can catch them), never a host fallback.
    from agentnode_sdk.runtimes.agent_llm_broker import host_llm_broker
    from agentnode_sdk.runtimes.agent_llm_policy import make_policy_broker, resolve_llm_policy

    try:
        from agentnode_sdk.config import load_config
        host_cfg = load_config() or {}
    except Exception:
        host_cfg = {}
    policy_broker = make_policy_broker(resolve_llm_policy(agent_config, host_cfg), host_llm_broker)

    # Fail-closed: a sandbox-START failure (e.g. the runtime vanished between the
    # availability check and the launch) returns a clean sandbox_unavailable —
    # it never raises out and never falls back to the host.
    try:
        session = backend.open_agent_session(spec)
    except Exception as exc:
        return _fail(f"Could not start the agent sandbox: {exc}")

    try:
        host = AgentRpcHost(
            allowed_packages=allowed or [],
            max_tool_calls=max_tool_calls,
            llm_broker=policy_broker,
        )
        out = host.run(
            session,
            init={"entrypoint": entrypoint, "goal": effective_goal, "kwargs": kwargs},
            timeout=timeout,
        )
    except Exception as exc:
        return RunToolResult(
            success=False, error=f"Sandboxed agent failed: {exc}",
            mode_used="agent_sandbox", run_id=run_id,
        )
    finally:
        session.close()

    res = out.get("result") or {}
    if res.get("ok"):
        return RunToolResult(success=True, result=res.get("value"), mode_used="agent_sandbox", run_id=run_id)
    return RunToolResult(
        success=False, error=res.get("error", "sandboxed agent failed"),
        mode_used="agent_sandbox", run_id=run_id,
    )
