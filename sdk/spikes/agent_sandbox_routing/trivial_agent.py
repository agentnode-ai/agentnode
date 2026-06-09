"""Trivial throwaway agents (as source strings) executed INSIDE the sandbox.

They are passed to the wrapper as plain source and exec'd there, so the container
needs neither the SDK nor a mounted package. Each one exercises the spike's two
hard problems and probes for host leakage.
"""
from __future__ import annotations

# Exercises A (tool-call via host RPC) + B (LLM via host broker) and probes that
# the container sees no host env / no host file.
AGENT_SOURCE = r'''
def run(context, **kwargs):
    import os
    saw_env = os.environ.get("AGENTNODE_HOST_SENTINEL")
    try:
        with open("/host-secret.txt") as f:
            saw_file = f.read()
    except Exception:
        saw_file = None
    tool = context.run_tool("spike-allowed-pack", text="hello")
    llm = context.call_llm([{"role": "user", "content": "ping"}])
    return {
        "tool": tool,
        "llm": llm,
        "saw_host_env": saw_env,
        "saw_host_file": saw_file,
    }
'''

# Requests a tool NOT in the agent's allowlist — the HOST must refuse it.
NONALLOWED_AGENT_SOURCE = r'''
def run(context, **kwargs):
    try:
        context.run_tool("evil-pack", text="x")
        return {"refused": False}
    except Exception as exc:
        return {"refused": True, "error": str(exc)}
'''
