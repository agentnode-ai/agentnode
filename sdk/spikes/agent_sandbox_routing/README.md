# Agent Sandbox Routing Spike (throwaway)

**Status: spike complete — local protocol AND real container isolation both verified
(Hetzner, docker + pinned image, 2026-06-09).**
This is a deletable prototype, NOT production code, NOT wired into `run_agent`.
It de-risks the two hard problems of the future agent-sandbox bow.

## What it does
Runs a trivial agent's entrypoint as an isolated process that talks a tiny
newline-delimited JSON protocol over stdio. The agent's `run_tool`/`call_llm`
calls are serviced by the **host**:

- **A — tool-calls cross the boundary and are decided host-side.** The host checks
  the agent allowlist + tool-call limit, then routes to the **real**
  `agentnode_sdk.runner.run_tool` (full trust/sandbox/guard pipeline). The
  container's self-reported limits are ignored.
- **B — LLM without secrets in the container.** `call_llm` is answered by a fake
  host-side broker; the container never sees an API key.

Two backends: `local` (plain subprocess, INSECURE, protocol verification only)
and `container` (real `ContainerBackend`, hardened, `network=none`, `env={}`,
`mounts=[]`, `clean_home`).

## How to run
```
# protocol mechanics (no Docker needed):
AGENTNODE_AGENT_SPIKE=1 python -m pytest tests/test_agent_sandbox_spike.py -q
# container isolation runs only where a runtime + the pinned image are present
# (e.g. the Hetzner host); it self-skips otherwise.
```

## Files (all throwaway)
- `container_agent_wrapper.py` — SDK-free in-container wrapper (`WRAPPER_SOURCE`); ports `_CONTAINER_WRAPPER`'s "save real stdout" trick, made bidirectional, + a `StdioProxyContext`.
- `host_driver.py` — spawns the wrapper (container or local), runs the request/response loop, services `run_tool` (host allowlist/limit → real `runner.run_tool`) + `call_llm` (fake broker), kills/cleans the container.
- `trivial_agent.py` — agent source strings (happy path + non-allowlisted probe).
- `fake_llm.py` — deterministic echo broker.
- `../../tests/test_agent_sandbox_spike.py` — gated by `AGENTNODE_AGENT_SPIKE=1`.

## Findings (local backend, this machine — no Docker)
- **Works.** The single-threaded ping-pong over stdio is stable; **no deadlock**
  (each side strictly alternates write→read). The init payload carries the agent
  source, so the container needs **no SDK and no mounts**.
- **A confirmed (with a defense-in-depth bonus).** The agent's
  `run_tool("spike-allowed-pack")` reached the host, passed the host allowlist,
  and the host called the real `runner.run_tool` — which itself returned
  `mode_used="sandbox_unavailable"` (no Docker here → the community pack is
  fail-closed). So even an agent tool-call that crosses the boundary still hits
  the full sandbox gate. Events serviced host-side: `run_tool`, `call_llm`.
- **B confirmed.** `call_llm` → `{"content": "[fake-llm] ping"}`; no key in the
  agent process.
- **Allowlist host-owned.** `run_tool("evil-pack")` → refused host-side
  (`tool 'evil-pack' not in agent allowlist`) **before** touching the runner;
  the container could not widen its own permissions.
- **Latency.** RPC transport overhead is negligible (~ms). The measured ~0.8s on
  the first call is the real `runner.run_tool` pipeline (sandbox availability
  probe), not stdio. Total for a 2-RPC agent: ~1.0s. → transport is not the
  bottleneck; the real tool pipeline is.

## Findings (container backend, Hetzner — real docker + pinned image)
Ran `AGENTNODE_AGENT_SPIKE=1 pytest tests/test_agent_sandbox_spike.py -v` in a fresh
venv with `agentnode-sdk==0.11.4` from PyPI and the pinned sandbox image pulled
anon (`sha256:6c77…c80f`). `ContainerBackend().check_available()` → `available=True,
backend=docker, image_available=True`. **All 3 tests passed (container test GREEN,
not skipped).**
- **Isolation confirmed.** With `AGENTNODE_HOST_SENTINEL` set in the host process:
  `saw_host_env == None` (env not inherited) and `saw_host_file == None` (no host
  FS). The agent ran in the image with `network=none`.
- **Hardened flags actually applied** (verified from the real `wrap_command` argv):
  `run -i --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges --user
  1000:1000 --pids-limit --memory --cpus --network none --tmpfs … --name --label`.
  `-i` keeps stdin open for the stdio control channel.
- **A (defense-in-depth, even stronger here).** The agent's `run_tool` crossed the
  boundary and the host called the real `runner.run_tool`, which returned
  `mode_used="policy_prompt"` ("Unverified package … requires approval") — i.e. the
  tool-call hit the real **policy/guard** gate host-side, not just availability.
- **B.** `call_llm` → `[fake-llm] ping`; no API key in the container.
- **Allowlist host-owned.** `evil-pack` refused host-side before the runner.
- **Latency.** RPC ~0.14s then ~0s; **total 0.71s including docker start** for a
  2-RPC agent. Transport is not the bottleneck.
- **Cleanup.** No leftover `agentnode-agent-spike-*` containers (`--rm` + explicit
  `rm -f`); services untouched.

## Transport recommendation
- **stdout-NDJSON is sufficient** for Python-level agents: redirecting the agent's
  `sys.stdout`/`sys.stderr` to capture buffers keeps the control channel clean.
- **Open risk:** a native extension or a child process writing directly to fd 1
  could still corrupt the stream. This spike did NOT hit that, but it is the
  reason to keep a **dedicated control channel (fd 3 or a unix socket)** on the
  table — adopt it only if a real agent is shown to pollute stdout. For now,
  stdout-NDJSON is the recommended starting transport.

## Verdict & next decisions (before any production work)
- **Viable.** The architecture is sound and mostly composes three existing,
  proven patterns (MCP container stdio + `_CONTAINER_WRAPPER` framing +
  `_ProxyAgentContext`/`_ipc_parent_loop` retargeted to stdio). The agent
  container can run `network=none` precisely because both tools and LLM are
  host-RPC'd — strictly stronger isolation than the MCP path.
- **Decisions for the real bow:** (1) transport — stdout-NDJSON vs fd-3/socket
  (only if pollution observed); (2) real credential-broker design (host holds the
  key; per-agent scope/rate/cost); (3) fallback when no runtime (fail-closed vs
  trusted-on-host bridge — ties to Docker-free runtime); (4) whether to refactor
  `_ProxyAgentContext`/`_ipc_parent_loop` into a transport-abstracted base reused
  by both the multiprocessing path and this stdio path.
- **Closed:** the `container` isolation assertions ran GREEN on Hetzner (no host
  env, no host file, `network=none`, hardened flags applied, tool-call through the
  real host-side policy gate, LLM host-brokered, ~0.7s total). The spike's core
  claim — an agent CAN run isolated with tools+LLM only via host-RPC — is proven.

This spike's job is done: it turns "hard/unknown" into "viable, with these four
decisions". It should be deleted (or folded into a design doc) before the real
agent-sandbox bow starts.
