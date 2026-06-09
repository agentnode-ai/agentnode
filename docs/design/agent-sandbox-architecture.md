# Agent Sandbox — Production Architecture

> Status: **design + interfaces (Sprint A).** This documents the target
> architecture and the transport-agnostic session/RPC core. None of it is wired
> into `run_agent` yet — Sprint A lays the rails only. The behaviour-changing
> work (a local backend, `run_agent` routing, community agents) is Sprint B+.
> Basis: the proven throwaway spike `sdk/spikes/agent_sandbox_routing/`.

## Problem
Toolpacks and MCP community code already run "isolated or not at all". An
**agent's own orchestration code** still runs on the host, gated only by
`trust >= trusted`. The spike proved an agent can instead run inside a sandbox
with its tool-calls and LLM calls routed back to the host via RPC — but shipping
that must not make AgentNode hostage to local Docker.

## Three deployment locations (the spine)
One pluggable backend, three places the sandbox process can run, chosen by a
`sandbox.mode` setting:

1. **Local** — the sandbox runs on the user's PC (Docker/Podman today; later
   bubblewrap/WASM/a bundled runtime).
2. **User-owned cloud** — the sandbox runs on the **user's own server** via a
   remote backend. Solves the local-Docker/VT-x blocker and keeps data on the
   user's infrastructure.
3. **Managed AgentNode cloud** — AgentNode hosts the sandbox as a bookable paid
   service. Simplest setup; explicit data-leaves-machine consent.

The wire protocol is identical in all three; only **where** the sandbox runs and
**which transport** carries the RPC differs.

## Layers
- **`AgentSandboxSession`** — a transport-agnostic bidirectional channel
  (`send` / `recv` / `close`). Local = the container's stdio; user-cloud /
  managed = an authenticated, encrypted socket. The RPC rides over this.
- **RPC protocol** — versioned, line-framed JSON messages: `init`, `run_tool`,
  `call_llm`, `result`, `error` (extensible: `use_credential`, `next_iteration`).
  Strict single request/response. Every message from the agent is untrusted
  input; the host validates and decides.
- **`AgentRpcHost`** — the host side of the loop. Services the agent's requests
  by enforcing the allowlist + tool-call limit **host-side** and routing
  `run_tool` to the real gated `runner.run_tool` and `call_llm` to the credential
  broker. The agent (in the sandbox) is only a requester; it can neither widen
  its permissions nor see secrets.
- **Backends** (one `SandboxBackend` interface, a session method added in
  Sprint B): `LocalContainerBackend`, `RemoteUserCloudBackend`,
  `ManagedCloudBackend` — each provides an `AgentSandboxSession`.

## Security model (from the spike, to be preserved)
Container has no host env, no host FS, `network=none` (feasible because tools
**and** LLM are host-RPC'd), hardened flags. The host owns allowlist, tool-call
limit, trust, and policy. No API key ever crosses the RPC to the agent — the
credential broker (Sprint C) holds keys host-side, injects them, and returns only
results, scoped per agent with rate/cost limits and audit.

## Target trust policy (`agent_execution_mode`)
A pure function maps trust to execution (encoded in Sprint A, **adopted in
Sprint B** — today's `run_agent` is unchanged):

- `curated` → **host** (vetted; may remain host).
- `trusted` → **sandbox** (migrate from today's host, with a transition window).
- `verified` / `unverified` (community) → **sandbox mandatory or fail-closed**
  (the unlock: community agents become runnable *when sandboxed*, instead of
  fully refused). Never host.
- unknown / missing → **refused** (fail-closed).

## Migration (don't break trusted agents)
Behind a `agent_sandbox` feature flag (default OFF = today's behaviour). Staged:
(1) add the backend + routing, community still refused → zero behaviour change;
(2) enable community-agent sandboxing — community runs iff a backend is
available, else fail-closed; trusted/curated unchanged; (3) migrate trusted
host→sandbox with a deprecation window, `sandbox doctor` warnings, and a
temporary opt-back. Cloud modes are opt-in throughout. Clear fail-closed
messages; catalog marks "requires sandbox".

## Sprint map
- **A (this) — Architecture + RPC core.** Design doc; `AgentSandboxSession` +
  `FakeSession`; RPC protocol + `AgentRpcHost`; `agent_execution_mode`; unit
  tests. New, isolated, un-wired. No behaviour change.
- **B — Local Agent Sandbox behind a flag.** `open_agent_session` on
  `SandboxBackend`; `LocalContainerBackend.open_agent_session`; wire `run_agent`
  behind `agent_sandbox` (community/verified → local sandbox-or-fail-closed;
  trusted/curated unchanged); gated security E2E.
- **C — Credential broker (minimal).** Host-held creds, `call_llm`/
  `use_credential` with scope + rate + cost + audit; real provider; no secret to
  the agent.
- **D — User-owned remote backend.** `RemoteUserCloudBackend` over an
  authenticated/encrypted transport to a sandbox-agent on the user's server.
- **E — Managed cloud backend.** `ManagedCloudBackend` to AgentNode's hosted
  service (auth, quota/billing, consent); paid.

## Open decisions
Transport robustness for local stdio (framed-stdio vs fd-3 vs unix-socket vs
always-socket); when/whether to migrate `trusted` off host; managed-mode key
custody (BYO-proxy vs entrust-AgentNode); whether to generalize toolpack/MCP to
the three modes too; protocol versioning/compat; whether `curated` is ever
sandboxed.

## Reuse from existing code
`_ProxyAgentContext` / `_ipc_parent_loop` (`runtimes/agent_runner.py`) are the
multiprocessing-queue ancestor of `AgentRpcHost` — the production host is their
transport-abstracted form. `ContainerBackend.wrap_command` already produces the
hardened argv the local session will `Popen`. `AgentContext.run_tool`
(allowlist + limit) + `runner.run_tool` (trust/sandbox/guard) are the real
enforcement the host RPC routes to.
