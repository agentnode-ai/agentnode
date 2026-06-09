# Agent Execution Boundary

> Status: **documented contract** (Sprint 1 of the Agent-Execution-Vector bow).
> This describes how agent code is executed **today** and where the security
> boundary is consciously drawn. It is locked by regression tests in
> `sdk/tests/test_agent_runner.py` (`TestAgentExecutionBoundary` +
> `TestRunAgentExecutionVectorInvariant`). No runtime behaviour is changed by
> this document — it makes the existing contract explicit and testable.

## The three foreign-code execution paths

| Path | Isolation today |
|---|---|
| **Toolpack** run | community → SandboxBackend container, or fail-closed (P0.0–P0.3) |
| **MCP** server | community → SandboxBackend container, or fail-closed (P0.2) |
| **Agent** entrypoint (orchestration code) | **runs on the HOST**, gated only by `trust >= trusted` |

Agents are the one foreign-code path whose **own** code is not routed through
`SandboxBackend`. This is acceptable today because of the trust gate below, but
it is the open item the future agent-sandbox bow must close.

## How an agent runs today (verified against code)

1. **Single entry.** `runner.run_tool` (`sdk/agentnode_sdk/runner.py:294-299`) is
   the only production path that runs an agent: when
   `entry.get("package_type") == "agent"` it calls
   `run_agent(slug, entry=entry, **kwargs)`. The `entry` (including
   `trust_level`) is read from the lockfile (`runner.py:124`) — callers cannot
   forge it.
2. **Trust gate (the security invariant).** `run_agent`
   (`runtimes/agent_runner.py:1254-1255`) reads
   `entry.get("trust_level", "unverified")` and refuses anything below
   `trusted` via `_trust_meets_minimum(trust_level, "trusted")`. Only `trusted`
   and `curated` proceed; `None`/`unverified`/`verified`/unknown are refused.
3. **Host execution.** After the gate, `_load_agent_entrypoint`
   (`agent_runner.py:1352/1521`, `importlib.import_module` + `getattr`) loads the
   agent function, which is run on the host via `_execute_with_timeout`
   (daemon thread, **default** `isolation="thread"`) or `_execute_with_process`
   (multiprocessing child). **Neither uses SandboxBackend.**
4. **Environment exposure.** In thread mode the agent shares the process and
   sees the **full host `os.environ`**; in process mode it gets a full copy.
   There is **no env allowlist** for agents (unlike the toolpack
   `python_runner._ENV_ALLOWLIST`). `_load_agentnode_env`
   (`agent_runner.py:164-182`) loads `~/.agentnode/.env` into `os.environ`, and
   `_auto_detect_llm` (`:244-289`) means the agent can see host LLM API keys,
   HOME and the working directory.
5. **Tool calls are sandboxed.** When agent code calls a tool via
   `context.run_tool → _dispatch_tool` (`agent_runner.py:913-948`), it
   **re-enters** `agentnode_sdk.runner.run_tool` — i.e. the agent's *tool calls*
   go through the normal trust + sandbox + guard pipeline. The gap is only the
   agent's **own orchestration code**.

## The contract

- An agent's own entrypoint code runs **on the host**, with host env/secrets.
- Therefore an agent may only run when **`trust_level ∈ {trusted, curated}`**.
- `verified`, `unverified`, unknown and missing trust → **refused** (no path runs
  community/unverified agent code).
- Trust is read from the **lockfile entry**, never from caller kwargs.
- An agent's **tool calls** are fully gated/sandboxed (they re-enter `run_tool`).
- Nested / sequential sub-agents go through `run_tool` and **re-apply** the gate.

## Risk assessment

- **No acute hole.** An adversarial review found no path to agent-entrypoint
  execution that bypasses the trust gate. Community/unverified/verified agents
  cannot run through any path today.
- **Residual exposure:** `trusted`/`curated` (vetted) agents run orchestration
  code on the host with the full host environment. This is the same conscious
  "trusted = host (P0.1 transition)" stance used for trusted toolpacks, with one
  wart: agents see the *full* host env rather than a toolpack-style allowlist.
- **Strategic risk:** this contradicts the eventual goals "sandbox trusted too"
  and "allow community agents". Either of those turns this into a **P0** and is
  the **trigger** for the agent-sandbox bow.

## Why not sandbox agent code now

Routing an agent's entrypoint through `SandboxBackend` (like toolpacks/MCP) is a
multi-sprint effort blocked on two hard problems:

- **A — agent⇆host tool-call RPC.** Inside a container, `context.run_tool` must
  marshal back to the host runner (which itself sandboxes the tool). Today only a
  multiprocessing-queue proxy (`_ProxyAgentContext`) exists; a container needs a
  new stdio JSON-RPC bridge.
- **B — LLM + credential brokering.** Agents call an LLM (network + API key); a
  sandbox has no host secrets, and credential brokering is explicitly deferred
  (the MCP runner already refuses `env_keys` for sandboxed packages).

Plus **C** — routing agents through the sandbox makes them require a container
runtime (fail-closed without one), which must be reconciled with the future
Docker-free runtime work via a pluggable isolation backend.

## Roadmap

1. **Sprint 1 (this doc + tests).** Document the boundary; lock the invariants
   with regression tests. No runtime/behaviour change.
2. **Sprint 2 — Agent Sandbox Routing Spike (throwaway).** Prototype the stdio
   JSON-RPC tool-call bridge + LLM-as-host-RPC for one trivial agent in a
   container; output a design + resolved/scoped risks for A/B/C.
3. **Sprint 3+ (gated on the spike) — full agent-sandbox bow.** Pluggable
   isolation backend, credential broker, fail-closed/fallback policy, lifecycle
   for long-running agents.

See [[project_agent_exec_vector]] in the maintainer memory for the live status.
