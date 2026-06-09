"""Gated test for the throwaway agent-sandbox routing spike.

STRICTLY opt-in: the whole module is skipped unless AGENTNODE_AGENT_SPIKE=1, so
the normal suite never depends on this spike (and never needs Docker/Podman).
Run it explicitly:

    AGENTNODE_AGENT_SPIKE=1 python -m pytest tests/test_agent_sandbox_spike.py -q

The two `local`-backend tests prove the RPC mechanics WITHOUT a container; the
`container` test proves real isolation and is skipped when no runtime is present.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTNODE_AGENT_SPIKE") != "1",
    reason="agent-sandbox spike is gated behind AGENTNODE_AGENT_SPIKE=1",
)

# Make the (non-shipped) spike package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "spikes"))


def _runtime_available() -> bool:
    # Use the REAL ContainerBackend, not the suite's faked default backend.
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    return ContainerBackend().check_available().available


def test_protocol_local_subprocess():
    """A+B mechanics without a container: stdio RPC loop, host-side run_tool
    routing, and the fake-LLM host broker."""
    from agent_sandbox_routing.host_driver import run_spike
    from agent_sandbox_routing.trivial_agent import AGENT_SOURCE

    out = run_spike(
        AGENT_SOURCE, goal="spike", allowed_packages=["spike-allowed-pack"],
        backend="local", timeout=30,
    )
    res = out["result"]
    assert res["ok"] is True, res
    value = res["value"]
    # B: LLM answered host-side (no key in the "container")
    assert value["llm"]["content"].startswith("[fake-llm]")
    # A: tool-call routed to the REAL runner.run_tool (uninstalled -> structured
    # failure result; the point is the host routed there).
    assert "success" in value["tool"]
    assert any(e[0] == "run_tool" for e in out["events"])
    assert any(e[0] == "call_llm" for e in out["events"])


def test_host_refuses_non_allowlisted_tool():
    """The host owns the allowlist — a tool the container requests but the agent
    is not allowed to use is refused host-side."""
    from agent_sandbox_routing.host_driver import run_spike
    from agent_sandbox_routing.trivial_agent import NONALLOWED_AGENT_SOURCE

    out = run_spike(
        NONALLOWED_AGENT_SOURCE, allowed_packages=["spike-allowed-pack"],
        backend="local", timeout=30,
    )
    value = out["result"]["value"]
    assert value["refused"] is True
    assert any(e[0] == "refused_allowlist" for e in out["events"])


def test_container_isolation():
    """Real container: no host env, no host file, network=none."""
    if not _runtime_available():
        pytest.skip("no container runtime (docker/podman) available")
    from agent_sandbox_routing.host_driver import run_spike
    from agent_sandbox_routing.trivial_agent import AGENT_SOURCE

    os.environ["AGENTNODE_HOST_SENTINEL"] = "host-secret-should-not-leak"
    out = run_spike(
        AGENT_SOURCE, goal="spike", allowed_packages=["spike-allowed-pack"],
        backend="container", timeout=60,
    )
    value = out["result"]["value"]
    assert value["saw_host_env"] is None       # host env NOT inherited
    assert value["saw_host_file"] is None       # host FS NOT readable
    assert value["llm"]["content"].startswith("[fake-llm]")
