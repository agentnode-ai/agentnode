"""B2a: route community (verified/unverified) agents through a sandboxed session,
behind a default-OFF feature flag.

Policy (only when the flag is ON):
  verified / unverified  -> sandbox-or-fail-closed (this module); NEVER host.
  trusted / curated      -> host (handled in run_agent, unchanged).
  unknown / missing       -> refused (run_agent's existing gate).

This is the FIRST behaviour change of the agent-sandbox bow. With the flag OFF
(default) nothing here runs and run_agent behaves exactly as before. No host
fallback: if no backend/volume is available, fail-closed. Tool-calls go host-side
through the real ``runner.run_tool`` (via AgentRpcHost); LLM calls go through the
host-side broker (B2b-1) behind a default-DENY credential policy (C1), and every
sandboxed run leaves ONE aggregated, sanitized audit record (C2). See
docs/design/agent-sandbox-architecture.md.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid

logger = logging.getLogger("agentnode.agent_sandbox")


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


def _audit_sandbox_run(slug, *, trust_level, run_id, success, reason,
                       policy=None, usage=None, events=None) -> None:
    """C2: ONE aggregated audit record per sandboxed agent run (reuses
    ``policy.audit_decision``; no new storage). Sanitized by construction:
    fixed reason codes, counters, and caps only — never prompts, keys, raw
    provider errors, or agent-authored error text. Never crashes the caller."""
    try:
        from agentnode_sdk.policy import PolicyResult, audit_decision

        llm = None
        if policy is not None:
            llm = {
                "enabled": policy.enabled,
                "max_calls": policy.max_calls,
                "max_input_chars": policy.max_input_chars,
                "max_output_chars": policy.max_output_chars,
                "allowed_models": (sorted(policy.allowed_models)
                                   if policy.allowed_models is not None else None),
            }
            llm.update(usage or {})
        ev = events or []
        audit_decision(
            PolicyResult(action="allow" if success else "deny",
                         reason=reason, source="agent_sandbox"),
            "agent_run",
            slug,
            trust_level=trust_level,
            run_id=run_id,
            extra={
                "sandbox": True,
                "llm": llm,
                "tool_calls": sum(1 for e in ev if e and e[0] == "run_tool"),
                "tool_refusals": sum(
                    1 for e in ev if e and e[0] in ("refused_allowlist", "refused_limit")),
            },
        )
    except Exception:
        logger.debug("Failed to audit sandboxed agent run: %s", slug, exc_info=True)


def run_agent_sandboxed(slug, entry, agent_config, *, goal=None, run_id=None, **kwargs):
    """Run a community agent's entrypoint inside the sandbox. Returns a
    RunToolResult. Fail-closed on a missing/stale volume or no runtime — never
    runs the agent on the host."""
    from agentnode_sdk.models import RunToolResult
    from agentnode_sdk.sandbox import get_default_backend, sandbox_volume_name
    from agentnode_sdk.sandbox.agent_container_wrapper import WRAPPER_SOURCE
    from agentnode_sdk.sandbox.agent_rpc import AgentRpcHost
    from agentnode_sdk.sandbox.types import MountSpec, ProcessSpec

    def _fail(error: str, mode: str = "sandbox_unavailable",
              reason_code: str = "fail_closed") -> "RunToolResult":
        # C2: fail-closed refusals get an audit record too (deny + fixed code).
        _audit_sandbox_run(slug, trust_level=(entry or {}).get("trust_level"),
                           run_id=run_id, success=False, reason=reason_code)
        return RunToolResult(success=False, error=error, mode_used=mode, run_id=run_id)

    agent_config = agent_config or {}
    entrypoint = agent_config.get("entrypoint", "")
    if not entrypoint:
        return _fail(f"Agent '{slug}' has no entrypoint defined.",
                     mode="agent_sandbox", reason_code="no_entrypoint")

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
            f"(run: agentnode install {slug}).",
            reason_code="volume_missing",
        )

    backend = get_default_backend()
    avail = backend.check_available()
    if not avail.available:
        return _fail(
            "Agent execution requires a container runtime + the pinned image. "
            f"{avail.reason or 'none available'} — refusing to run community agent "
            "code on the host.",
            reason_code="backend_unavailable",
        )

    runtime = avail.backend or "docker"
    try:
        insp = subprocess.run(
            [runtime, "volume", "inspect", expected_vol],
            capture_output=True, timeout=10,
        )
    except Exception as exc:
        return _fail(f"Could not verify sandbox volume: {exc}",
                     reason_code="volume_inspect_failed")
    if insp.returncode != 0:
        return _fail(f"Agent sandbox volume missing — reinstall '{slug}'.",
                     reason_code="volume_missing")

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
    policy = resolve_llm_policy(agent_config, host_cfg)
    policy_broker = make_policy_broker(policy, host_llm_broker)

    # Fail-closed: a sandbox-START failure (e.g. the runtime vanished between the
    # availability check and the launch) returns a clean sandbox_unavailable —
    # it never raises out and never falls back to the host.
    try:
        session = backend.open_agent_session(spec)
    except Exception as exc:
        return _fail(f"Could not start the agent sandbox: {exc}",
                     reason_code="session_start_failed")

    host = AgentRpcHost(
        allowed_packages=allowed or [],
        max_tool_calls=max_tool_calls,
        llm_broker=policy_broker,
    )
    try:
        out = host.run(
            session,
            init={"entrypoint": entrypoint, "goal": effective_goal, "kwargs": kwargs},
            timeout=timeout,
        )
    except Exception as exc:
        _audit_sandbox_run(slug, trust_level=entry.get("trust_level"), run_id=run_id,
                           success=False, reason=f"host_loop_error: {type(exc).__name__}",
                           policy=policy, usage=policy_broker.usage, events=host.events)
        return RunToolResult(
            success=False, error=f"Sandboxed agent failed: {exc}",
            mode_used="agent_sandbox", run_id=run_id,
        )
    finally:
        session.close()

    res = out.get("result") or {}
    events = out.get("events") or []
    if res.get("ok"):
        _audit_sandbox_run(slug, trust_level=entry.get("trust_level"), run_id=run_id,
                           success=True, reason="agent_completed",
                           policy=policy, usage=policy_broker.usage, events=events)
        return RunToolResult(success=True, result=res.get("value"), mode_used="agent_sandbox", run_id=run_id)
    _audit_sandbox_run(slug, trust_level=entry.get("trust_level"), run_id=run_id,
                       success=False, reason="agent_error",
                       policy=policy, usage=policy_broker.usage, events=events)
    return RunToolResult(
        success=False, error=res.get("error", "sandboxed agent failed"),
        mode_used="agent_sandbox", run_id=run_id,
    )
