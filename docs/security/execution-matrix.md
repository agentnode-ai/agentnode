# Execution Matrix

Where each kind of package runs — host, sandbox, or refused — as a function of
its trust tier and the two user-facing controls. This is the single reference
for "what happens when I run this?"; the per-mechanism details live in
[agent-execution-boundary.md](agent-execution-boundary.md) and
[host-trust-policy.md](host-trust-policy.md).

## The two controls

- **`sandbox.host_trust_policy`** — how much the *host-eligible* tiers
  (`trusted`, `curated`) are trusted to run directly on the host:
  - `curated_only` — **shipped default**: only `curated` on the host; trusted
    third-party code is sandboxed.
  - `default` — more permissive: `curated` and `trusted` run on the host.
  - `none` — nothing on the host; `curated` and `trusted` are sandboxed too.

  An existing config keeps whatever value it already records; the shipped default
  governs new installations.
- **`agent_sandbox.enabled`** (default **ON** since 0.21.0) — whether community
  agents may run at all. ON → community agents run sandbox-or-refuse. OFF →
  community agents are refused outright (the pre-0.21 behavior).

"Sandbox" always means **sandbox-or-refuse**: the container when a runtime and
the pinned image are present, otherwise a clean refusal. There is **never a host
fallback** for a tier that requires the sandbox.

## Matrix (under the shipped default, `curated_only`)

| Package kind | `curated` | `trusted` | `verified` (community) | `unverified` / unknown (community) |
|---|---|---|---|---|
| **Skill** (prompt-only) | host¹ | host¹ | host¹ | host¹ |
| **Tool pack** (code) | host | **sandbox-or-refuse** | sandbox-or-refuse | sandbox-or-refuse |
| **MCP server** (code) | host² | **sandbox-or-refuse** | sandbox-or-refuse | sandbox-or-refuse |
| **Agent** (code) | **refused**⁴ | **refused**⁴ | sandbox-or-refuse³ | sandbox-or-refuse³ |

Under the more permissive `default` policy, the `trusted` column becomes `host`
for tool packs and MCP servers. Nothing else changes.

¹ **Skills execute no code** — they are prompt-only (`runtime: none`,
`install_mode: prompt_only`, no entrypoint), enforced by the publish validator.
There is nothing to sandbox, so a skill never needs the sandbox regardless of
tier. This is why skills show "No sandbox needed" everywhere.

² Under the shipped `curated_only` default, only curated MCP servers run on the
host; trusted third-party and community MCP servers are sandbox-or-refuse. See
the MCP routing notes for credential brokering.

³ Community agents are gated by `agent_sandbox.enabled` (default ON). With it
OFF, the community-agent cells become **refused** (no sandbox attempt).

⁴ **In the `run_tool` → `run_agent` path, the host route for an agent's own
entrypoint is a structural refusal**, at every tier and under every policy value.
`run_agent` reaches `refuse_host_agent_execution()` in `exceptions.py`, which
raises `HostAgentExecutionUnsupported` (error code
`host_agent_execution_unsupported`) before any import, spawn, environment read or
IPC can occur. There is no flag, environment variable, config value or
monkeypatch that re-enables it. A token scan of `agent_runner.py` finds no
`Process(`, `Thread(`, `Popen(`, `importlib` or `runpy` — a statement about that
file, established by scanning it.

The host-trust policy does not participate in this: it routes tool packs and MCP
servers, and can add sandbox routing for `trusted`/`curated` agents, but it never
makes an agent entrypoint host-eligible.

The one host-side agent path that still works is **declarative sequential
orchestration** (`agent.orchestration.mode == "sequential"`): it runs no foreign
entrypoint, only declared tool steps, and each step is re-gated through
`run_tool` exactly like a direct tool call. A curated or trusted agent package
that declares such an orchestration therefore still runs; one that ships its own
entrypoint is refused.

## How the controls shift the host-eligible tiers

`host_trust_policy` only moves `curated`/`trusted` between *host* and *sandbox*;
it never lets a community tier onto the host.

| Tier | `default` | `curated_only` | `none` |
|---|---|---|---|
| `curated` | host | host | sandbox |
| `trusted` | host | sandbox | sandbox |
| community (`verified`/`unverified`/unknown) | sandbox-or-refuse | sandbox-or-refuse | sandbox-or-refuse |

This table is about **tool packs and MCP servers**. It does not apply to agent
entrypoints: those are refused on the host under every policy (footnote 4), so
for agents the `default` column is not a host path.

**Under the shipped `curated_only` default, the only foreign code that still runs
on your machine outside a container is `curated` — AgentNode's own packages.**
Setting `none` sandboxes those too. Setting `default` additionally puts trusted
third-party tool packs and MCP servers back on the host, with your environment.

Every sandboxed tier is fail-closed: without a container runtime those packages
refuse to run rather than falling back to the host.

## Invariants

- **No host fallback for community code.** A community tool pack, MCP server, or
  agent either runs inside the container or is refused — it is never run on the
  host because the sandbox was unavailable.
- **Fail-closed.** A missing runtime, missing image, missing/stale sandbox
  volume, or an unknown network mode results in a refusal, never a silent
  downgrade to host execution.
- **Host secrets stay on the host.** Sandboxed code receives a minimal
  environment (`PYTHONPATH` only for agents); LLM keys are used by a host-side
  broker and never enter the container; tool calls are brokered host-side where
  the allowlist and policy are enforced.

## Platform support levels

The sandbox boundary is **a Linux container**. The SDK reaches it through the
`docker` or `podman` CLI; backend detection probes for that CLI, a reachable
daemon and the pinned image, and contains no operating-system branch. So the
boundary is the same wherever a Linux container runtime is present — on Windows
and macOS, Docker Desktop supplies it through its own Linux VM.

A platform is called **SUPPORTED** only when CI exercises **the boundary** on it
— not merely the SDK's logic tests. Being able to *operate* AgentNode from a
browser or a phone is not support: it says nothing about where foreign code runs.

**No platform meets that bar today.** Every real container end-to-end suite is
gated behind an environment variable — `AGENTNODE_SANDBOX_E2E`,
`AGENTNODE_AGENT_SANDBOX_E2E`, `AGENTNODE_EGRESS_E2E` — and **no CI workflow sets
any of them**. The Ubuntu job proves the SDK's logic tests pass on Linux; it does
not exercise the container isolating anything. Until a CI job runs those suites
with a real runtime and the pinned image, the boundary is unverified everywhere.

| platform | level | why |
|---|---|---|
| Linux (desktop and server) with a container runtime | **EXPERIMENTAL** | logic tests run on `ubuntu-latest`, but **no CI run exercises the container boundary** |
| Windows with a container runtime | **EXPERIMENTAL** | the boundary is the same Linux container via Docker Desktop, but **no Windows CI job exists** |
| macOS with a container runtime | **EXPERIMENTAL** | same reasoning; **no macOS CI job exists** |
| WSL2 / local Linux VM | **EXPERIMENTAL** | the ordinary Linux path applies inside them; not separately verified |
| Windows or macOS **without** a container runtime | **UNSUPPORTED** | community and (under `curated_only`) trusted tiers are sandbox-or-refuse, so they refuse |
| Managed external sandbox / remote worker | **UNSUPPORTED** | not built — there is no remote execution backend yet |
| Android, iOS | **UNSUPPORTED** | no local execution path; iOS forbids arbitrary code execution outright |
| Phone as a client of a remote sandbox | **UNSUPPORTED** | depends on the remote backend above |

Offline use works only if the pinned image was pulled beforehand
(`agentnode sandbox pull`); there is no auto-pull, and a missing image is a
refusal, never a host fallback.

## Known limitation (tracked)

`trust_level` is stored in the lockfile as a mutable field (so the periodic
trust-refresh can update it) and is **not** covered by the unkeyed integrity
hash. A local attacker with write access to the lockfile could forge a higher
tier; under the `default` policy that would grant host execution. Mitigations
today: tighten `sandbox.host_trust_policy`, and the periodic refresh corrects
the value from the registry. The robust fix (a registry-signed trust
attestation bound to the sealed `artifact_hash`) is scoped as a separate,
future change.
