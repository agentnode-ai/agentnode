# Agent Execution Boundary

> Status: **documented contract**, verified against the code it describes.
> Locked by regression tests in `sdk/tests/test_agent_runner.py`
> (`TestAgentExecutionBoundary`, `TestRunAgentExecutionVectorInvariant`).
> This document changes no runtime behaviour; it states the contract the code
> already enforces.

> **Correction (2026-09-01).** Earlier revisions of this page described agent
> entrypoints as running **on the host** via `_load_agent_entrypoint`,
> `_execute_with_timeout` (daemon thread) and `_execute_with_process`
> (multiprocessing), and listed the agent⇆host RPC bridge and LLM brokering as
> unsolved blockers. **All of that is out of date.** Those functions no longer
> exist, host entrypoint execution has been removed, and both blockers were
> subsequently built. The current contract is below; the superseded description
> is kept only as history in the last section.

## The three foreign-code execution paths

| Path | Isolation today |
|---|---|
| **Tool pack** run | community and `trusted` → container, or fail-closed. `curated` → **host subprocess** under the shipped `curated_only` default |
| **MCP** server | community and `trusted` → container, or fail-closed. `curated` → **host process** under the shipped default |
| **Agent** entrypoint | community → container, or fail-closed. `curated`/`trusted` → **refused** |

The agent path is now the **most** restricted of the three, not the least.

## How an agent runs today

1. **Single entry.** `runner.run_tool` is the only production path that runs an
   agent. The `entry` — including `trust_level` — is read from the lockfile;
   callers cannot forge it.
2. **One policy decision, made once.** `run_tool` reads the host-trust policy
   snapshot and calls `enforce_sandbox_policy`, producing an immutable
   `HostTrustPolicyDecision`. That object is threaded to `run_agent`, which
   **refuses** a missing or entry-mismatched decision and never re-reads the
   policy or re-derives the matrix.
3. **Community agents** (`verified`, `unverified`, unknown) are gated by
   `agent_sandbox.enabled` (default **ON**): sandbox-or-refuse, never a host
   fallback. With the flag OFF they are refused outright.
4. **`trusted` and `curated` agents** take the host route when the policy leaves
   their tier host-eligible — and in `run_agent` that host route is a **structural
   refusal**. `refuse_host_agent_execution()` in `exceptions.py` is the chokepoint
   this path reaches; it raises `HostAgentExecutionUnsupported` (error code
   `host_agent_execution_unsupported`) **before** any import, spawn, environment
   read, or agent/tool/LLM context is created. There is no flag, environment
   variable, config value or monkeypatch that turns it on. When the policy instead
   sandboxes their tier, they run in the container agent session.
5. **The one host-side agent path that still runs** is declarative sequential
   orchestration (`agent.orchestration.mode == "sequential"`). It imports no
   foreign entrypoint: it dispatches declared tool steps, and every step
   re-enters `run_tool` and is re-gated exactly like a direct tool call.

A token scan of `agent_runner.py` finds no `Process(`, `Thread(`, `Popen(`,
`importlib` or `runpy`: in that file the host executor was removed, not merely
disabled. The scan covers that file, and the statement is scoped to it.

## How a sandboxed agent works

The agent's entrypoint is imported **inside** the container, from a read-only
`/pack` volume, by an SDK-free wrapper (`sandbox/agent_container_wrapper.py`).
The wrapper speaks a line-oriented JSON protocol over stdin/stdout and redirects
the agent's own stdout/stderr into a capture buffer so they cannot corrupt the
control channel. It also neutralises `fork`/`exec`/`subprocess` at the Python
level as defence in depth — the container flags (`--cap-drop=ALL`,
`--read-only`, `--user 1000:1000`, `--pids-limit`, `--network none`) are the real
boundary.

Two things the agent asks for are answered **on the host**, never inside the
container (`sandbox/agent_rpc.py`):

- **`run_tool`** is routed to the real gated `runner.run_tool`, with the
  allowlist and the tool-call limit enforced host-side.
- **`call_llm`** is routed to a host-side credential broker
  (`runtimes/agent_llm_broker.host_llm_broker`), so LLM API keys stay on the
  host. With no broker configured, `call_llm` is refused.

## The contract

Scope of these statements: they describe the **documented production run path**,
`runner.run_tool` → `run_agent`, and are traceable to the sources named beside
them. They are deliberately not phrased as claims about every conceivable route
into the codebase.

- **Within the `run_tool` → `run_agent` path, the host route for an agent's own
  entrypoint is a structural refusal.** `run_agent` reaches
  `refuse_host_agent_execution()` (`exceptions.py`), which raises before any
  import, spawn, environment read or IPC. There is no flag, environment variable,
  config value or monkeypatch that turns it on.
- **`agent_runner.py` holds no host-execution primitive.** A token scan of that
  file finds no `Process(`, `Thread(`, `Popen(`, `importlib` or `runpy`. This is a
  statement about that file, established by scanning it — not about other modules.
- Community agents run in the container or not at all — there is no host fallback
  when the runtime or the pinned image is missing.
- `trusted`/`curated` agents that ship an entrypoint are refused. Those that
  declare a sequential orchestration still run, because no foreign code loads:
  the declared tool steps are re-gated through `run_tool` individually.
- The host-trust policy decides host-versus-sandbox routing for tool packs and
  MCP servers, and can add sandbox routing for `trusted`/`curated` agents. It does
  **not** decide whether an agent entrypoint may run on the host, and it does not
  govern community agents, which follow the separate `agent_sandbox.enabled` flag.
- Trust is read from the lockfile entry, not from caller kwargs.
- An agent's tool calls re-enter `run_tool` and are gated there.
- Nested and sequential sub-agents go through `run_tool` and re-apply the gate.
- Host secrets do not enter the container; the LLM key is used host-side.

## Residual risk

- **The agent path is not the exposure any more.** Under the shipped
  `curated_only` default the residual host-execution vector is **curated tool
  packs and MCP servers** — AgentNode's own code. Selecting the more permissive
  `default` policy puts trusted third-party code back on the host. See
  [execution-matrix.md](execution-matrix.md).
- **`trust_level` is mutable in the lockfile** and is not covered by the unkeyed
  integrity hash. A local attacker with write access could forge a higher tier.
  A registry-signed trust attestation is the robust fix and is a separate,
  unstarted piece of work.
- **Windows and macOS are unverified.** The container boundary is the same there
  via Docker Desktop, but no CI job exercises the SDK on either.

## Superseded history

The earlier "why not sandbox agent code now" analysis named three blockers:
**A** an agent⇆host tool-call RPC bridge, **B** LLM and credential brokering, and
**C** the resulting hard dependency on a container runtime.

A and B were built: the stdio JSON bridge is `sandbox/agent_rpc.py` plus
`sandbox/agent_container_wrapper.py`, and the host-side broker is
`runtimes/agent_llm_broker.py`. C was accepted rather than solved — sandboxed
execution **is** fail-closed on a missing runtime, deliberately.

The roadmap in earlier revisions (a throwaway spike, then a full agent-sandbox
bow) is complete for the agent path and is no longer the live plan. The current
execution-model plan and its decisions are recorded outside the shipped docs.
