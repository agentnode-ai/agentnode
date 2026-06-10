"""B2b-2 — GATED end-to-end proof of the agent sandbox (real container).

NOT part of the normal suite. Gated by ``AGENTNODE_AGENT_SANDBOX_E2E=1`` AND a real
container runtime + the pinned image; without the flag the whole module SKIPS, so
``pytest tests/`` stays Docker-free.

In ONE real container run of the actual ``run_agent -> run_agent_sandboxed`` path it
proves, for a community (unverified) agent with the sandbox flag ON:
  - tool-RPC crosses the boundary and the HOST owns the decision
    (the non-allowlisted ``e2e-evil-pack`` is refused host-side, before any runner),
  - LLM-RPC round-trips through a deterministic host-side FAKE broker
    (no real provider, no API key),
  - full isolation: no host env, no host FS, no network egress, no provider key
    inside the container,
  - no host fallback (only ``agent_sandbox``/``sandbox_unavailable`` ever reported),
  - clean teardown (no leftover container/volume).

Determinism note: the LLM broker and the tool RUNNER are faked via monkeypatch
(no real provider, no installed pack, no host-side raise turning into a fail-close).
The host-side ALLOWLIST gate is REAL — only the tool execution is faked. The real
run_tool gate is proven separately (spike + unit + clean-machine tests).
"""
from __future__ import annotations

import os
import subprocess

import pytest

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.runtimes.agent_runner import run_agent
from agentnode_sdk.sandbox import sandbox_volume_name
from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, ContainerBackend

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTNODE_AGENT_SANDBOX_E2E") != "1",
    reason="gated: set AGENTNODE_AGENT_SANDBOX_E2E=1 (needs a container runtime + pinned image)",
)

# The community agent that runs INSIDE the container. Stdlib + the injected
# `context` only — no SDK import. `__HOST_SECRET_PATH__` is substituted at seed time.
_AGENT_SOURCE = '''
import os, socket

def run(context, **kwargs):
    result = {}
    # tool-RPC: an allowlisted pack -> host runner decides (faked, deterministic)
    try:
        result["tool"] = context.run_tool("e2e-allowlisted-pack", tool_name="echo", x=1)
    except Exception as e:
        result["tool_error"] = repr(e)
    # non-allowlisted pack -> host-side allowlist refusal (REAL, before the runner)
    try:
        context.run_tool("e2e-evil-pack")
        result["evil"] = "ALLOWED"
    except Exception:
        result["evil"] = "refused"
    # LLM-RPC via the host-side (fake) broker
    result["llm"] = context.call_llm([{"role": "user", "content": "ping"}])
    # isolation probes
    result["saw_host_env"] = os.environ.get("AGENTNODE_E2E_HOST_SENTINEL")
    result["saw_provider_key"] = (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    )
    try:
        with open("__HOST_SECRET_PATH__") as f:
            result["saw_host_file"] = f.read()
    except Exception:
        result["saw_host_file"] = None
    try:
        c = socket.create_connection(("1.1.1.1", 53), timeout=2)
        c.close()
        result["network_egress"] = "open"
    except Exception:
        result["network_egress"] = "blocked"
    return result
'''


def test_agent_sandbox_e2e(monkeypatch, tmp_path):
    backend = ContainerBackend()
    avail = backend.check_available()
    if not avail.available:
        pytest.skip("no container runtime + pinned image")
    runtime = avail.backend or "docker"

    def rt(*args, **kw):
        return subprocess.run([runtime, *args], capture_output=True, text=True, **kw)

    slug, version, ahash = "e2e-comm-agent", "1.0.0", "sha256:e2e"
    vol = sandbox_volume_name(slug, version, ahash)

    # A host-side secret file the container must NOT be able to read.
    host_secret = tmp_path / "host_secret.txt"
    host_secret.write_text("host-fs-secret")
    agent_src = _AGENT_SOURCE.replace("__HOST_SECRET_PATH__", str(host_secret))

    agent_config = {
        "entrypoint": "comm_agent:run",
        "goal": "e2e",
        "limits": {"max_tool_calls": 5, "max_runtime_seconds": 60},
        "tool_access": {"allowed_packages": ["e2e-allowlisted-pack"]},
        # C1: LLM is opt-in (default-deny); the E2E agent uses call_llm, so it must
        # declare llm_access. The host-config ceiling would still clamp this.
        "llm_access": {"enabled": True},
    }
    entry = {
        "version": version, "artifact_hash": ahash, "trust_level": "unverified",
        "package_type": "agent", "sandboxed": True, "sandbox_volume": vol,
        "agent": agent_config,
    }

    try:
        # --- seed the per-version volume with the agent module ---
        rt("volume", "create", vol)
        # Seed as root: a fresh volume is root-owned and the pinned image's default
        # user (uid 1000) cannot write to it. This mirrors how install builds the
        # volume; the agent RUN itself still uses the hardened --user 1000:1000.
        seed = rt(
            "run", "--rm", "-i", "--user", "0:0", "--entrypoint", "sh",
            "-v", f"{vol}:/pack", _BASE_IMAGE, "-c", "cat > /pack/comm_agent.py",
            input=agent_src,
        )
        assert seed.returncode == 0, f"volume seed failed: {seed.stderr}"

        # --- flag ON + host sentinels + deterministic fakes (no real provider/pack) ---
        monkeypatch.setenv("AGENTNODE_AGENT_SANDBOX", "1")
        monkeypatch.setenv("AGENTNODE_E2E_HOST_SENTINEL", "host-only-secret")

        def _fake_broker(messages):
            last = messages[-1]["content"] if messages else ""
            return {"role": "assistant", "content": "[fake-llm] " + last}

        def _fake_run_tool(slug_, tool_name=None, **kwargs):
            return RunToolResult(
                success=True, result={"slug": slug_, "echo": kwargs}, mode_used="host"
            )

        monkeypatch.setattr(
            "agentnode_sdk.runtimes.agent_llm_broker.host_llm_broker", _fake_broker
        )
        monkeypatch.setattr("agentnode_sdk.runner.run_tool", _fake_run_tool)

        # --- run the REAL routing path ---
        res = run_agent(slug, entry=entry)

        # --- mode + success ---
        assert res.success is True, f"run failed: {res.error}"
        assert res.mode_used == "agent_sandbox"
        r = res.result or {}

        # --- LLM-RPC (fake broker round-trip) ---
        assert isinstance(r.get("llm"), dict)
        assert r["llm"]["content"].startswith("[fake-llm]")

        # --- tool-RPC: allowlisted runs (host owns it), non-allowlisted refused host-side ---
        assert isinstance(r.get("tool"), dict), r
        assert r["tool"].get("success") is True
        assert r["tool"]["result"]["slug"] == "e2e-allowlisted-pack"
        assert r.get("evil") == "refused"

        # --- isolation: no host env / no provider key / no host FS / no network ---
        assert r.get("saw_host_env") is None
        assert r.get("saw_provider_key") is None
        assert r.get("saw_host_file") is None
        assert r.get("network_egress") == "blocked"

        # --- no leftover container (spec uses --rm) ---
        leftover = rt("ps", "-aq", "--filter", "name=agentnode-agent-").stdout.strip()
        assert leftover == "", f"leftover agent container(s): {leftover}"
    finally:
        # teardown: never touch host services — only the test's own container/volume
        for cid in rt("ps", "-aq", "--filter", "name=agentnode-agent-").stdout.split():
            rt("rm", "-f", cid)
        rt("volume", "rm", "-f", vol)
