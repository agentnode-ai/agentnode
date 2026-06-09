"""Sprint B1 — backend agent-session capability (additive, inert to run_agent).

Docker-free unit tests drive ``_ContainerAgentSession`` + the in-container wrapper
over a plain local subprocess (no Docker), proving the protocol + fail-closed +
that ``run_agent`` is still un-wired. The real container-isolation test is gated
behind ``AGENTNODE_AGENT_SANDBOX_E2E=1`` and a runtime (verified on a Docker host
like Hetzner) — it self-skips otherwise, so the normal suite never needs Docker.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agentnode_sdk.sandbox.agent_container_wrapper import WRAPPER_SOURCE
from agentnode_sdk.sandbox.agent_rpc import AgentRpcHost
from agentnode_sdk.sandbox.backend import NoSandboxBackend
from agentnode_sdk.sandbox.container_backend import ContainerBackend, _ContainerAgentSession
from agentnode_sdk.sandbox.types import MountSpec, ProcessSpec, SandboxRequiredError


def _local_session(tmp_path: Path):
    """A _ContainerAgentSession over a plain `python -c WRAPPER` subprocess that
    imports the agent from tmp_path (mimics the /pack volume). No Docker."""
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-c", WRAPPER_SOURCE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )
    return _ContainerAgentSession(proc)


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------

def test_no_sandbox_backend_open_agent_session_fail_closed():
    spec = ProcessSpec(command=["python", "-c", "pass"])
    with pytest.raises(SandboxRequiredError):
        NoSandboxBackend().open_agent_session(spec)


def test_container_backend_open_agent_session_fail_closed_without_runtime():
    # Force a non-existent runtime → check_available False → fail-closed.
    be = ContainerBackend(runtime="definitely-not-a-real-runtime")
    spec = ProcessSpec(command=["python", "-c", "pass"])
    with pytest.raises(SandboxRequiredError):
        be.open_agent_session(spec)


# ---------------------------------------------------------------------------
# Session + wrapper protocol (Docker-free, local subprocess)
# ---------------------------------------------------------------------------

def test_session_protocol_via_local_subprocess(tmp_path):
    (tmp_path / "b1_agent.py").write_text(textwrap.dedent("""
        def run(context, **kwargs):
            t = context.run_tool("allowed-pack", text="hi")
            l = context.call_llm([{"role": "user", "content": "ping"}])
            return {"tool": t, "llm": l}
    """))
    session = _local_session(tmp_path)
    try:
        host = AgentRpcHost(
            allowed_packages=["allowed-pack"], max_tool_calls=5,
            tool_runner=lambda slug, tool, kw: {"ran": slug, "kw": kw},
            llm_broker=lambda msgs: {"role": "assistant", "content": "[fake] " + msgs[-1]["content"]},
        )
        out = host.run(session, init={"entrypoint": "b1_agent:run", "goal": "spike", "kwargs": {}}, timeout=15)
    finally:
        session.close()
    assert out["result"]["ok"] is True, out
    value = out["result"]["value"]
    assert value["tool"]["ran"] == "allowed-pack"
    assert value["llm"]["content"].startswith("[fake]")
    assert [e[0] for e in out["events"]] == ["run_tool", "call_llm"]


def test_session_allowlist_refused_hostside(tmp_path):
    (tmp_path / "b1_evil.py").write_text(textwrap.dedent("""
        def run(context, **kwargs):
            try:
                context.run_tool("evil-pack")
                return {"refused": False}
            except Exception:
                return {"refused": True}
    """))
    session = _local_session(tmp_path)
    try:
        host = AgentRpcHost(
            allowed_packages=["allowed-pack"], max_tool_calls=5,
            tool_runner=lambda slug, tool, kw: {"ran": slug},
        )
        out = host.run(session, init={"entrypoint": "b1_evil:run", "kwargs": {}}, timeout=15)
    finally:
        session.close()
    assert out["result"]["value"]["refused"] is True
    assert any(e[0] == "refused_allowlist" for e in out["events"])


# ---------------------------------------------------------------------------
# Inert-to-run_agent guard
# ---------------------------------------------------------------------------

def test_run_agent_does_not_use_agent_session():
    """B1 must NOT wire the session into run_agent."""
    import agentnode_sdk
    src = (Path(agentnode_sdk.__file__).parent / "runtimes" / "agent_runner.py").read_text(encoding="utf-8")
    for tok in ("open_agent_session", "agent_container_wrapper", "AgentSandboxSession", "AgentRpcHost"):
        assert tok not in src, f"agent_runner.py references {tok!r} — B1 must stay un-wired"


# ---------------------------------------------------------------------------
# Real container isolation (gated; verified on a Docker host)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("AGENTNODE_AGENT_SANDBOX_E2E") != "1",
    reason="gated: set AGENTNODE_AGENT_SANDBOX_E2E=1 (needs a container runtime + image)",
)
def test_container_isolation_real():
    be = ContainerBackend()
    if not be.check_available().available:
        pytest.skip("no container runtime + pinned image")
    runtime = be.check_available().backend
    image = be._image
    vol = "agentnode-b1-spike-vol"
    name = "agentnode-b1-spike"
    agent_src = (
        "def run(context, **kwargs):\n"
        "    import os\n"
        "    t = context.run_tool('allowed-pack')\n"
        "    l = context.call_llm([{'role':'user','content':'ping'}])\n"
        "    saw_file = open('/host-secret.txt').read() if os.path.exists('/host-secret.txt') else None\n"
        "    return {'tool': t, 'llm': l, 'saw_env': os.environ.get('AGENTNODE_HOST_SENTINEL'), 'saw_file': saw_file}\n"
    )
    subprocess.run([runtime, "volume", "rm", "-f", vol], capture_output=True)
    subprocess.run([runtime, "volume", "create", vol], capture_output=True, check=True)
    # seed the agent into the fresh volume as root (the image's default user 1000
    # cannot write a freshly-created root-owned volume)
    subprocess.run(
        [runtime, "run", "--rm", "--user", "0:0", "-v", f"{vol}:/pack", image,
         "python", "-c", f"open('/pack/b1_agent.py','w').write({agent_src!r})"],
        capture_output=True, check=True,
    )
    spec = be.build_process_spec(
        ["python", "-c", WRAPPER_SOURCE],
        network="none", env={"PYTHONPATH": "/pack"},
        mounts=[MountSpec(src=vol, dst="/pack", read_only=True)],
        clean_home=True, interactive=True,
    )
    spec.name = name
    os.environ["AGENTNODE_HOST_SENTINEL"] = "leak-me-not"
    session = be.open_agent_session(spec)
    try:
        host = AgentRpcHost(
            allowed_packages=["allowed-pack"], max_tool_calls=5,
            tool_runner=lambda s, t, k: {"ran": s},
            llm_broker=lambda m: {"content": "[fake] ok"},
        )
        out = host.run(session, init={"entrypoint": "b1_agent:run", "kwargs": {}}, timeout=60)
    finally:
        session.close()
        subprocess.run([runtime, "volume", "rm", "-f", vol], capture_output=True)
    value = out["result"]["value"]
    assert value["saw_env"] is None       # no host env leaked into the container
    assert value["saw_file"] is None       # no host FS
    assert value["llm"]["content"].startswith("[fake]")
